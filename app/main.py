from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .binance import completed_window
from .config import get_settings
from .db import connect, fetch_all, fetch_one
from .storage import iter_download

VERSION = '1.2.0'
app = FastAPI(title='Binance 10% Executable Grid Research', version=VERSION)
templates = Jinja2Templates(directory=str(Path(__file__).parent / 'templates'))
security = HTTPBasic(auto_error=False)


def auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    settings = get_settings()
    if not settings.admin_password:
        return
    if not credentials or credentials.password != settings.admin_password:
        raise HTTPException(status_code=401, headers={'WWW-Authenticate': 'Basic'})


@app.get('/health')
def health() -> dict[str, str]:
    return {
        'status': 'ok',
        'version': VERSION,
        'event_definition': 'executable_entry_10pct_within_8h_on_complete_15m_grid',
    }


@app.get('/', response_class=HTMLResponse, dependencies=[Depends(auth)])
def dashboard(request: Request) -> HTMLResponse:
    settings = get_settings()
    grids = fetch_all(settings, 'select * from binance10_grid_jobs order by created_at desc limit 30') if settings.configured else []
    exports = fetch_all(settings, 'select * from binance10_export_jobs order by created_at desc limit 30') if settings.configured else []
    files = fetch_all(
        settings,
        "select * from binance10_grid_files order by export_job_id,case when role='index' then 0 else 1 end,split,filename",
    ) if settings.configured else []
    return templates.TemplateResponse(request, 'index.html', {
        'version': VERSION,
        'configured': settings.configured,
        'grids': grids,
        'exports': exports,
        'files': files,
    })


@app.post('/grid', dependencies=[Depends(auth)])
def create_grid(
    lookback_days: int = Form(60),
    historical_start: str = Form(''),
    historical_end_exclusive: str = Form(''),
    min_entry_notional: float = Form(500),
    min_exit_notional: float = Form(500),
) -> Response:
    settings = get_settings()
    if bool(historical_start) != bool(historical_end_exclusive):
        raise HTTPException(400, 'Provide both historical dates or leave both blank')
    if historical_start:
        start = date.fromisoformat(historical_start)
        end = date.fromisoformat(historical_end_exclusive)
    else:
        start_dt, end_dt = completed_window(lookback_days)
        start, end = start_dt.date(), end_dt.date()
    if start >= end:
        raise HTTPException(400, 'Start must be before end')
    if (end - start).days < 10:
        raise HTTPException(400, 'Use at least 10 days so chronological splits and embargoes are meaningful')
    if (end - start).days > 365:
        raise HTTPException(400, 'A single run is limited to 365 days')
    if min_entry_notional < 0 or min_exit_notional < 0:
        raise HTTPException(400, 'Liquidity thresholds cannot be negative')
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into binance10_grid_jobs(
                  status,window_start_date,window_end_date_exclusive,lookback_days,
                  cadence_minutes,threshold_pct,horizon_minutes,entry_liquidity_minutes,
                  exit_liquidity_minutes,min_entry_notional,min_exit_notional,quote_assets,protocol_version
                ) values ('queued',%s,%s,%s,15,10,480,15,5,%s,%s,%s::jsonb,'binance10_v1_2_executable_grid')
                returning id
                """,
                (start, end, lookback_days, min_entry_notional, min_exit_notional, json.dumps(list(settings.quote_assets))),
            )
            job_id = str(cur.fetchone()['id'])
        conn.commit()
    return Response(status_code=303, headers={'Location': f'/?queued_grid={job_id}'})


@app.post('/export', dependencies=[Depends(auth)])
def create_export(grid_job_id: str = Form(...)) -> Response:
    settings = get_settings()
    source = fetch_one(settings, 'select status from binance10_grid_jobs where id=%s', (grid_job_id,))
    if not source or source['status'] not in {'completed', 'completed_with_warnings'}:
        raise HTTPException(400, 'The candidate-grid job must complete before raw evidence is exported')
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into binance10_export_jobs(
                  grid_job_id,status,prior_days,high_res_hours,symbols_per_shard,protocol_version
                ) values (%s,'queued',10,48,8,'binance10_v1_2_executable_grid') returning id
                """,
                (grid_job_id,),
            )
            job_id = str(cur.fetchone()['id'])
        conn.commit()
    return Response(status_code=303, headers={'Location': f'/?queued_export={job_id}'})


@app.post('/retry/{kind}/{job_id}', dependencies=[Depends(auth)])
def retry_job(kind: str, job_id: str) -> Response:
    settings = get_settings()
    with connect(settings) as conn:
        with conn.cursor() as cur:
            if kind == 'grid':
                cur.execute('delete from binance10_candidates where grid_job_id=%s', (job_id,))
                cur.execute('delete from binance10_grid_issues where grid_job_id=%s', (job_id,))
                cur.execute(
                    "update binance10_grid_jobs set status='queued',started_at=null,completed_at=null,heartbeat_at=null,"
                    "symbols_total=0,symbols_processed=0,candidates_total=0,target_reached_count=0,"
                    "actionable_count=0,failures=0,result_json=null,error_message=null where id=%s",
                    (job_id,),
                )
            elif kind == 'export':
                cur.execute('delete from binance10_grid_files where export_job_id=%s', (job_id,))
                cur.execute('delete from binance10_grid_issues where export_job_id=%s', (job_id,))
                cur.execute(
                    "update binance10_export_jobs set status='queued',started_at=null,completed_at=null,heartbeat_at=null,"
                    "symbols_processed=0,files_created=0,raw_bar_rows=0,failures=0,result_json=null,error_message=null where id=%s",
                    (job_id,),
                )
            else:
                raise HTTPException(404, 'Unknown job type')
        conn.commit()
    return Response(status_code=303, headers={'Location': '/'})


@app.get('/download/{file_id}', dependencies=[Depends(auth)])
def download_file(file_id: str) -> Response:
    settings = get_settings()
    row = fetch_one(settings, 'select * from binance10_grid_files where id=%s', (file_id,))
    if not row:
        raise HTTPException(404, 'File not found')
    return StreamingResponse(
        iter_download(settings, row['storage_path']),
        media_type=row['content_type'],
        headers={'Content-Disposition': f"attachment; filename={row['filename']}"},
    )
