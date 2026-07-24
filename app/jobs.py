from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import traceback

from .binance import BinanceClient
from .config import Settings
from .controls import select_controls
from .db import connect, fetch_all, fetch_one
from .exporter import build_context_packages
from .features import SNAPSHOT_OFFSETS_MINUTES, feature_row, summary_match_metrics
from .scanner import candidate_groups, detect_events_from_minutes, enrich_saleability

logger = logging.getLogger(__name__)
UTC = timezone.utc




def recover_interrupted_jobs(settings: Settings) -> None:
    """Recover jobs left running when the single worker was restarted."""
    with connect(settings) as conn:
        with conn.cursor() as cur:
            # Scans can resume after the last fully processed symbol.
            cur.execute(
                "update binance10_scan_jobs set status='queued', started_at=null, heartbeat_at=null "
                "where status='running'"
            )
            # Downstream partial selections/packages are rebuilt deterministically.
            cur.execute("select id from binance10_control_jobs where status='running'")
            control_ids = [row['id'] for row in cur.fetchall()]
            for job_id in control_ids:
                cur.execute('delete from binance10_controls where control_job_id=%s', (job_id,))
                cur.execute('delete from binance10_issues where control_job_id=%s', (job_id,))
                cur.execute(
                    "update binance10_control_jobs set status='queued', started_at=null, heartbeat_at=null, "
                    "events_processed=0, controls_created=0, failures=0, error_message=null where id=%s",
                    (job_id,),
                )
            cur.execute("select id from binance10_context_jobs where status='running'")
            context_ids = [row['id'] for row in cur.fetchall()]
            for job_id in context_ids:
                cur.execute('delete from binance10_files where context_job_id=%s', (job_id,))
                cur.execute('delete from binance10_issues where context_job_id=%s', (job_id,))
                cur.execute(
                    "update binance10_context_jobs set status='queued', started_at=null, heartbeat_at=null, "
                    "events_processed=0, samples_total=0, feature_rows=0, failures=0, result_json=null, error_message=null where id=%s",
                    (job_id,),
                )
        conn.commit()


def _claim(settings: Settings, table: str) -> dict | None:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select * from {table}
                 where status='queued'
                 order by created_at
                 for update skip locked
                 limit 1
                """
            )
            row = cur.fetchone()
            if row:
                cur.execute(f"update {table} set status='running', started_at=now(), heartbeat_at=now() where id=%s", (row['id'],))
        conn.commit()
    return dict(row) if row else None


def _fail(settings: Settings, table: str, job_id: str, error: Exception) -> None:
    message = f'{type(error).__name__}: {error}\n{traceback.format_exc()}'[:12000]
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(f"update {table} set status='failed', completed_at=now(), error_message=%s where id=%s", (message, job_id))
        conn.commit()


def run_scan_job(settings: Settings, job: dict) -> None:
    client = BinanceClient(settings)
    job_id = str(job['id'])
    threshold_pct = Decimal(str(job['threshold_pct']))
    window_minutes = int(job['window_minutes'])
    cooldown_minutes = int(job['cooldown_minutes'])
    saleability_seconds = int(job['saleability_seconds'])
    min_exit_notional = Decimal(str(job['min_exit_notional']))
    start = datetime.combine(job['window_start_date'], datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(job['window_end_date_exclusive'], datetime.min.time(), tzinfo=UTC)
    symbols = client.active_spot_symbols(job['quote_assets'])
    if settings.max_symbols > 0:
        symbols = symbols[: settings.max_symbols]
    resume_from = min(int(job.get('symbols_processed') or 0), len(symbols))
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute('update binance10_scan_jobs set symbols_total=%s where id=%s', (len(symbols), job_id))
            cur.execute('select count(*) as n, count(*) filter (where saleable) as saleable_n from binance10_events where scan_job_id=%s', (job_id,))
            counts = cur.fetchone()
        conn.commit()

    event_count = int(counts['n'])
    saleable_count = int(counts['saleable_n'])
    for index, symbol_row in enumerate(symbols[resume_from:], start=resume_from + 1):
        symbol = symbol_row['symbol']
        try:
            coarse = client.klines(symbol, '15m', start - timedelta(minutes=window_minutes), end)
            groups = candidate_groups(coarse, threshold_pct, window_minutes)
            symbol_events = []
            for group_start, group_end in groups:
                minute_start = max(start - timedelta(minutes=window_minutes), group_start)
                minute_end = min(end, group_end + timedelta(seconds=saleability_seconds))
                minute_bars = client.klines(symbol, '1m', minute_start, minute_end)
                detected = detect_events_from_minutes(
                    symbol, symbol_row['base_asset'], symbol_row['quote_asset'], minute_bars,
                    threshold_pct, window_minutes, cooldown_minutes,
                )
                for event in detected:
                    if not (start <= event.crossing_time < end):
                        continue
                    if any(abs((event.crossing_time - old.crossing_time).total_seconds()) < cooldown_minutes * 60 for old in symbol_events):
                        continue
                    enriched = enrich_saleability(client, event, minute_bars, saleability_seconds, min_exit_notional)
                    symbol_events.append(enriched)
            with connect(settings) as conn:
                with conn.cursor() as cur:
                    for event in symbol_events:
                        event_key = f"{event.symbol}:{event.crossing_time.isoformat()}:{threshold_pct}:{window_minutes}"
                        cur.execute(
                            """
                            insert into binance10_events(
                              scan_job_id,event_key,symbol,base_asset,quote_asset,baseline_time,baseline_price,
                              crossing_time,crossing_bar_open,crossing_bar_high,threshold_price,gain_pct,minutes_to_cross,
                              exit_quote_notional,exit_trade_count,saleability_source,saleable
                            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            on conflict (scan_job_id,event_key) do nothing
                            """,
                            (job_id,event_key,event.symbol,event.base_asset,event.quote_asset,event.baseline_time,event.baseline_price,
                             event.crossing_time,event.crossing_bar_open,event.crossing_bar_high,event.threshold_price,event.gain_pct,
                             event.minutes_to_cross,event.exit_quote_notional,event.exit_trade_count,event.saleability_source,event.saleable),
                        )
                    cur.execute(
                        'select count(*) as n, count(*) filter (where saleable) as saleable_n from binance10_events where scan_job_id=%s',
                        (job_id,),
                    )
                    current_counts = cur.fetchone()
                    event_count = int(current_counts['n'])
                    saleable_count = int(current_counts['saleable_n'])
                    cur.execute(
                        'update binance10_scan_jobs set symbols_processed=%s, events_found=%s, saleable_events=%s, heartbeat_at=now() where id=%s',
                        (index, event_count, saleable_count, job_id),
                    )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception('Symbol scan failed: %s', symbol)
            with connect(settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "insert into binance10_issues(scan_job_id,symbol,stage,message) values (%s,%s,'scan',%s)",
                        (job_id, symbol, str(exc)[:4000]),
                    )
                    cur.execute('update binance10_scan_jobs set failures=failures+1, symbols_processed=%s, heartbeat_at=now() where id=%s', (index, job_id))
                conn.commit()
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update binance10_scan_jobs set status=case when failures>0 then 'completed_with_warnings' else 'completed' end, completed_at=now(), result_json=%s where id=%s",
                (json.dumps({'events_found': event_count, 'saleable_events': saleable_count}), job_id),
            )
        conn.commit()


def run_control_job(settings: Settings, job: dict) -> None:
    client = BinanceClient(settings)
    job_id = str(job['id'])
    scan = fetch_one(settings, 'select * from binance10_scan_jobs where id=%s', (job['scan_job_id'],))
    if not scan:
        raise RuntimeError('Source scan not found')
    events = fetch_all(settings, 'select * from binance10_events where scan_job_id=%s and saleable=true order by crossing_time', (job['scan_job_id'],))
    by_symbol: dict[str, list[dict]] = {}
    for event in events:
        by_symbol.setdefault(event['symbol'], []).append(event)
    total_controls = 0
    processed = 0
    for symbol, symbol_events in by_symbol.items():
        start = datetime.combine(scan['window_start_date'], datetime.min.time(), tzinfo=UTC) - timedelta(days=10)
        end = datetime.combine(scan['window_end_date_exclusive'], datetime.min.time(), tzinfo=UTC) + timedelta(minutes=int(scan['window_minutes']))
        try:
            bars = client.klines(symbol, '15m', start, end)
            event_times = [event['baseline_time'] for event in symbol_events]
            used_control_times: list[datetime] = []
            for event in symbol_events:
                event_metrics = summary_match_metrics(bars, event['baseline_time'])
                controls = select_controls(
                    bars, event['baseline_time'], event_metrics, [*event_times, *used_control_times],
                    int(job['controls_per_event']), float(scan['threshold_pct']), int(scan['window_minutes']),
                )
                used_control_times.extend(c['pseudo_baseline_time'] for c in controls)
                with connect(settings) as conn:
                    with conn.cursor() as cur:
                        for rank, control in enumerate(controls, start=1):
                            cur.execute(
                                """
                                insert into binance10_controls(
                                  control_job_id,event_id,symbol,pseudo_baseline_time,match_rank,match_score,
                                  ret_24h,rv_24h,qv_24h,ret_8h
                                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                on conflict (control_job_id,event_id,pseudo_baseline_time) do nothing
                                """,
                                (job_id,event['id'],symbol,control['pseudo_baseline_time'],rank,control['match_score'],
                                 control['ret_24h'],control['rv_24h'],control['qv_24h'],control['ret_8h']),
                            )
                        total_controls += len(controls)
                        processed += 1
                        cur.execute('update binance10_control_jobs set events_processed=%s, controls_created=%s, heartbeat_at=now() where id=%s', (processed,total_controls,job_id))
                    conn.commit()
        except Exception as exc:  # noqa: BLE001
            with connect(settings) as conn:
                with conn.cursor() as cur:
                    cur.execute("insert into binance10_issues(control_job_id,symbol,stage,message) values (%s,%s,'controls',%s)", (job_id,symbol,str(exc)[:4000]))
                    cur.execute('update binance10_control_jobs set failures=failures+1 where id=%s', (job_id,))
                conn.commit()
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute("update binance10_control_jobs set status=case when failures>0 then 'completed_with_warnings' else 'completed' end, completed_at=now() where id=%s", (job_id,))
        conn.commit()


def run_context_job(settings: Settings, job: dict) -> None:
    client = BinanceClient(settings)
    job_id = str(job['id'])
    control_job = fetch_one(settings, 'select * from binance10_control_jobs where id=%s', (job['control_job_id'],))
    scan = fetch_one(settings, 'select s.* from binance10_scan_jobs s join binance10_control_jobs c on c.scan_job_id=s.id where c.id=%s', (job['control_job_id'],))
    if not control_job or not scan:
        raise RuntimeError('Source jobs not found')
    events = fetch_all(settings, 'select * from binance10_events where scan_job_id=%s and saleable=true order by baseline_time', (control_job['scan_job_id'],))
    controls = fetch_all(settings, 'select * from binance10_controls where control_job_id=%s order by event_id,match_rank', (job['control_job_id'],))
    controls_by_event: dict[str, list[dict]] = {}
    for row in controls:
        controls_by_event.setdefault(str(row['event_id']), []).append(row)

    sample_rows: list[dict[str, object]] = []
    feature_rows: list[dict[str, object]] = []
    by_symbol: dict[str, list[dict]] = {}
    for event in events:
        by_symbol.setdefault(event['symbol'], []).append(event)

    all_anchors = [event['baseline_time'] for event in events]
    all_anchors.extend(control['pseudo_baseline_time'] for control in controls)
    reference_bars: dict[str, list] = {}
    if all_anchors:
        reference_start = min(all_anchors) - timedelta(days=10, minutes=15)
        reference_end = max(all_anchors) + timedelta(minutes=15)
        for ref_name, ref_symbol in {'BTC':'BTCUSDT','ETH':'ETHUSDT','BNB':'BNBUSDT'}.items():
            try:
                reference_bars[ref_name] = client.klines(ref_symbol, '15m', reference_start, reference_end)
            except Exception as exc:  # noqa: BLE001
                reference_bars[ref_name] = []
                with connect(settings) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "insert into binance10_issues(context_job_id,symbol,stage,message) values (%s,%s,'reference_context',%s)",
                            (job_id, ref_symbol, str(exc)[:4000]),
                        )
                        cur.execute('update binance10_context_jobs set failures=failures+1 where id=%s', (job_id,))
                    conn.commit()

    processed = 0
    for symbol, symbol_events in by_symbol.items():
        anchors = [event['baseline_time'] for event in symbol_events]
        for event in symbol_events:
            anchors.extend(c['pseudo_baseline_time'] for c in controls_by_event.get(str(event['id']), []))
        start = min(anchors) - timedelta(days=10, minutes=15)
        end = max(anchors) + timedelta(minutes=15)
        bars = client.klines(symbol, '15m', start, end)
        for event in symbol_events:
            event_sample_id = f"event:{event['id']}"
            sample_rows.append({
                'sample_id': event_sample_id,
                'label': 'event',
                'event_id': str(event['id']),
                'symbol': symbol,
                'anchor_time': event['baseline_time'].isoformat(),
                'outcome_crossing_time': event['crossing_time'].isoformat(),
                'outcome_minutes_to_cross': event['minutes_to_cross'],
                'outcome_gain_pct': float(event['gain_pct']),
                'outcome_exit_quote_notional': float(event['exit_quote_notional']),
                'outcome_saleable': event['saleable'],
            })
            for offset in SNAPSHOT_OFFSETS_MINUTES:
                feature_rows.append(feature_row(event_sample_id,'event',symbol,event['baseline_time'],bars,offset,reference_bars))
            for control in controls_by_event.get(str(event['id']), []):
                control_sample_id = f"control:{control['id']}"
                sample_rows.append({
                    'sample_id': control_sample_id,
                    'label': 'control',
                    'event_id': str(event['id']),
                    'control_id': str(control['id']),
                    'symbol': symbol,
                    'anchor_time': control['pseudo_baseline_time'].isoformat(),
                    'match_rank': control['match_rank'],
                    'match_score': float(control['match_score']),
                    'outcome_saleable': False,
                })
                for offset in SNAPSHOT_OFFSETS_MINUTES:
                    feature_rows.append(feature_row(control_sample_id,'control',symbol,control['pseudo_baseline_time'],bars,offset,reference_bars))
            processed += 1
            with connect(settings) as conn:
                with conn.cursor() as cur:
                    cur.execute('update binance10_context_jobs set events_processed=%s, samples_total=%s, feature_rows=%s, heartbeat_at=now() where id=%s', (processed,len(sample_rows),len(feature_rows),job_id))
                conn.commit()

    metadata = {
        'protocol_version': 'binance10_v1_rolling_8h',
        'created_at': datetime.now(tz=UTC).isoformat(),
        'threshold_pct': float(scan['threshold_pct']),
        'window_minutes': scan['window_minutes'],
        'cooldown_minutes': scan['cooldown_minutes'],
        'predictor_history_days': 10,
        'snapshot_offsets_minutes': list(SNAPSHOT_OFFSETS_MINUTES),
        'reference_context': ['BTCUSDT','ETHUSDT','BNBUSDT'],
        'event_definition': 'First one-minute high reaching 10% above the lowest low in the preceding completed eight hours; one event per symbol per eight-hour cooldown.',
        'control_definition': 'Same-symbol, same-UTC-slot pseudo-baseline with no 10% event in the surrounding event window; ranked by prior return, volatility and quote-volume similarity.',
        'limitations': [
            'Initial scans use the current Binance Spot trading universe and can omit delisted historical symbols.',
            'Historical order-book queues are unavailable; saleability uses executed aggregate-trade notional with one-minute quote-volume fallback.',
            'Controls are observational and do not prove causal relationships.',
        ],
    }
    package_manifest = build_context_packages(settings, job_id, sample_rows, feature_rows, metadata)
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update binance10_context_jobs set status=case when failures>0 then 'completed_with_warnings' else 'completed' end, completed_at=now(), result_json=%s where id=%s",
                (json.dumps({'packages':package_manifest,'samples':len(sample_rows),'feature_rows':len(feature_rows)}),job_id),
            )
        conn.commit()


def process_one(settings: Settings) -> bool:
    for table, runner in (
        ('binance10_scan_jobs', run_scan_job),
        ('binance10_control_jobs', run_control_job),
        ('binance10_context_jobs', run_context_job),
    ):
        job = _claim(settings, table)
        if job:
            try:
                runner(settings, job)
            except Exception as exc:  # noqa: BLE001
                logger.exception('Job failed: %s %s', table, job['id'])
                _fail(settings, table, str(job['id']), exc)
            return True
    return False
