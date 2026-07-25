from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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

app = FastAPI(title='Binance 10% Gainer Research', version='1.1.0')
templates = Jinja2Templates(directory=str(Path(__file__).parent / 'templates'))
security = HTTPBasic(auto_error=False)


def auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    settings = get_settings()
    if not settings.admin_password:
        return
    if not credentials or credentials.password != settings.admin_password:
        raise HTTPException(status_code=401, headers={'WWW-Authenticate':'Basic'})


@app.get('/health')
def health() -> dict[str, str]:
    return {'status':'ok','version':'1.1.0','event_definition':'10pct_within_8h'}


@app.get('/', response_class=HTMLResponse, dependencies=[Depends(auth)])
def dashboard(request: Request) -> HTMLResponse:
    settings = get_settings()
    scans = fetch_all(settings, 'select * from binance10_scan_jobs order by created_at desc limit 30') if settings.configured else []
    controls = fetch_all(settings, 'select * from binance10_control_jobs order by created_at desc limit 30') if settings.configured else []
    contexts = fetch_all(settings, 'select * from binance10_context_jobs order by created_at desc limit 30') if settings.configured else []
    files = fetch_all(settings, 'select * from binance10_files order by created_at desc limit 30') if settings.configured else []
    return templates.TemplateResponse(request, 'index.html', {
        'version':'1.1.0','configured':settings.configured,'scans':scans,'controls':controls,'contexts':contexts,'files':files,
    })


@app.post('/scan', dependencies=[Depends(auth)])
def create_scan(
    lookback_days: int = Form(60),
    historical_start: str = Form(''),
    historical_end_exclusive: str = Form(''),
    min_exit_notional: float = Form(500),
) -> Response:
    settings = get_settings()
    if bool(historical_start) != bool(historical_end_exclusive):
        raise HTTPException(400, 'Provide both historical dates or leave both blank')
    if historical_start and historical_end_exclusive:
        start = date.fromisoformat(historical_start)
        end = date.fromisoformat(historical_end_exclusive)
    else:
        start_dt, end_dt = completed_window(lookback_days)
        start, end = start_dt.date(), end_dt.date()
    if start >= end:
        raise HTTPException(400, 'Start must be before end')
    if (end - start).days > 365:
        raise HTTPException(400, 'A single scan is limited to 365 days')
    if min_exit_notional < 0:
        raise HTTPException(400, 'Minimum exit notional cannot be negative')
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into binance10_scan_jobs(
                  status,window_start_date,window_end_date_exclusive,lookback_days,threshold_pct,window_minutes,
                  cooldown_minutes,quote_assets,min_exit_notional,saleability_seconds,event_definition_version
                ) values ('queued',%s,%s,%s,10,480,480,%s::jsonb,%s,300,'binance10_v1_rolling_8h') returning id
                """,
                (start,end,lookback_days,json.dumps(list(settings.quote_assets)),min_exit_notional),
            )
            job_id = str(cur.fetchone()['id'])
        conn.commit()
    return Response(status_code=303, headers={'Location':f'/?queued_scan={job_id}'})


@app.post('/controls', dependencies=[Depends(auth)])
def create_controls(scan_job_id: str = Form(...), controls_per_event: int = Form(5)) -> Response:
    settings = get_settings()
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into binance10_control_jobs(scan_job_id,status,controls_per_event,prior_days) values (%s,'queued',%s,10) returning id",
                (scan_job_id,controls_per_event),
            )
            job_id = str(cur.fetchone()['id'])
        conn.commit()
    return Response(status_code=303, headers={'Location':f'/?queued_controls={job_id}'})


@app.post('/context', dependencies=[Depends(auth)])
def create_context(control_job_id: str = Form(...)) -> Response:
    settings = get_settings()
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into binance10_context_jobs(control_job_id,status,prior_days,protocol_version) values (%s,'queued',10,'binance10_v1_1_raw_evidence') returning id",
                (control_job_id,),
            )
            job_id = str(cur.fetchone()['id'])
        conn.commit()
    return Response(status_code=303, headers={'Location':f'/?queued_context={job_id}'})


@app.post('/retry/{kind}/{job_id}', dependencies=[Depends(auth)])
def retry_job(kind: str, job_id: str) -> Response:
    settings = get_settings()
    if kind not in {'scan','controls','context'}:
        raise HTTPException(404, 'Unknown job type')
    with connect(settings) as conn:
        with conn.cursor() as cur:
            if kind == 'scan':
                cur.execute(
                    "update binance10_scan_jobs set status='queued', completed_at=null, error_message=null where id=%s",
                    (job_id,),
                )
            elif kind == 'controls':
                cur.execute('delete from binance10_controls where control_job_id=%s', (job_id,))
                cur.execute('delete from binance10_issues where control_job_id=%s', (job_id,))
                cur.execute(
                    "update binance10_control_jobs set status='queued', started_at=null, completed_at=null, heartbeat_at=null, "
                    "events_processed=0, controls_created=0, failures=0, error_message=null where id=%s",
                    (job_id,),
                )
            else:
                cur.execute('delete from binance10_files where context_job_id=%s', (job_id,))
                cur.execute('delete from binance10_issues where context_job_id=%s', (job_id,))
                cur.execute(
                    "update binance10_context_jobs set status='queued', started_at=null, completed_at=null, heartbeat_at=null, "
                    "events_processed=0, samples_total=0, feature_rows=0, raw_bar_rows=0, failures=0, result_json=null, error_message=null where id=%s",
                    (job_id,),
                )
        conn.commit()
    return Response(status_code=303, headers={'Location':'/'})


@app.get('/download/{file_id}', dependencies=[Depends(auth)])
def download_file(file_id: str) -> Response:
    settings = get_settings()
    row = fetch_one(settings, 'select * from binance10_files where id=%s', (file_id,))
    if not row:
        raise HTTPException(404, 'File not found')
    return StreamingResponse(
        iter_download(settings, row['storage_path']),
        media_type=row['content_type'],
        headers={'Content-Disposition':f"attachment; filename={row['filename']}"},
    )
