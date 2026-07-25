from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import math
import traceback

from .binance import BinanceClient
from .config import Settings
from .controls import select_controls
from .db import connect, fetch_all, fetch_one
from .exporter import RawEvidencePackageBuilder
from .raw_evidence import (
    HIGH_RESOLUTION_HOURS,
    REFERENCE_SYMBOLS,
    SUBJECT_HISTORY_DAYS,
    completed_bars,
)
from .scanner import candidate_groups, detect_events_from_minutes, enrich_saleability

logger = logging.getLogger(__name__)
UTC = timezone.utc


def recover_interrupted_jobs(settings: Settings) -> None:
    """Recover jobs left running when the single worker was restarted."""
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update binance10_scan_jobs set status='queued', started_at=null, heartbeat_at=null "
                "where status='running'"
            )
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
                    "events_processed=0, samples_total=0, feature_rows=0, raw_bar_rows=0, failures=0, "
                    "result_json=null, error_message=null where id=%s",
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
                cur.execute(
                    f"update {table} set status='running', started_at=now(), heartbeat_at=now() where id=%s",
                    (row['id'],),
                )
        conn.commit()
    return dict(row) if row else None


def _fail(settings: Settings, table: str, job_id: str, error: Exception) -> None:
    message = f'{type(error).__name__}: {error}\n{traceback.format_exc()}'[:12000]
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update {table} set status='failed', completed_at=now(), error_message=%s where id=%s",
                (message, job_id),
            )
        conn.commit()


def _record_issue(
    settings: Settings,
    *,
    job_id: str,
    job_kind: str,
    symbol: str | None,
    stage: str,
    error: Exception,
) -> None:
    column = {
        'scan': 'scan_job_id',
        'controls': 'control_job_id',
        'context': 'context_job_id',
    }[job_kind]
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"insert into binance10_issues({column},symbol,stage,message) values (%s,%s,%s,%s)",
                (job_id, symbol, stage, str(error)[:4000]),
            )
            table = {
                'scan': 'binance10_scan_jobs',
                'controls': 'binance10_control_jobs',
                'context': 'binance10_context_jobs',
            }[job_kind]
            cur.execute(f'update {table} set failures=failures+1 where id=%s', (job_id,))
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
            cur.execute(
                'select count(*) as n, count(*) filter (where saleable) as saleable_n '
                'from binance10_events where scan_job_id=%s',
                (job_id,),
            )
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
                    symbol,
                    symbol_row['base_asset'],
                    symbol_row['quote_asset'],
                    minute_bars,
                    threshold_pct,
                    window_minutes,
                    cooldown_minutes,
                )
                for event in detected:
                    if not (start <= event.crossing_time < end):
                        continue
                    if any(
                        abs((event.crossing_time - old.crossing_time).total_seconds()) < cooldown_minutes * 60
                        for old in symbol_events
                    ):
                        continue
                    symbol_events.append(
                        enrich_saleability(client, event, minute_bars, saleability_seconds, min_exit_notional)
                    )
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
                            (
                                job_id, event_key, event.symbol, event.base_asset, event.quote_asset,
                                event.baseline_time, event.baseline_price, event.crossing_time,
                                event.crossing_bar_open, event.crossing_bar_high, event.threshold_price,
                                event.gain_pct, event.minutes_to_cross, event.exit_quote_notional,
                                event.exit_trade_count, event.saleability_source, event.saleable,
                            ),
                        )
                    cur.execute(
                        'select count(*) as n, count(*) filter (where saleable) as saleable_n '
                        'from binance10_events where scan_job_id=%s',
                        (job_id,),
                    )
                    current_counts = cur.fetchone()
                    event_count = int(current_counts['n'])
                    saleable_count = int(current_counts['saleable_n'])
                    cur.execute(
                        'update binance10_scan_jobs set symbols_processed=%s, events_found=%s, '
                        'saleable_events=%s, heartbeat_at=now() where id=%s',
                        (index, event_count, saleable_count, job_id),
                    )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception('Symbol scan failed: %s', symbol)
            _record_issue(
                settings, job_id=job_id, job_kind='scan', symbol=symbol, stage='scan', error=exc
            )
            with connect(settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        'update binance10_scan_jobs set symbols_processed=%s, heartbeat_at=now() where id=%s',
                        (index, job_id),
                    )
                conn.commit()
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update binance10_scan_jobs set status=case when failures>0 then 'completed_with_warnings' "
                "else 'completed' end, completed_at=now(), result_json=%s where id=%s",
                (json.dumps({'events_found': event_count, 'saleable_events': saleable_count}), job_id),
            )
        conn.commit()


def run_control_job(settings: Settings, job: dict) -> None:
    client = BinanceClient(settings)
    job_id = str(job['id'])
    scan = fetch_one(settings, 'select * from binance10_scan_jobs where id=%s', (job['scan_job_id'],))
    if not scan:
        raise RuntimeError('Source scan not found')
    events = fetch_all(
        settings,
        'select * from binance10_events where scan_job_id=%s and saleable=true order by crossing_time',
        (job['scan_job_id'],),
    )
    by_symbol: dict[str, list[dict]] = {}
    for event in events:
        by_symbol.setdefault(event['symbol'], []).append(event)
    total_controls = 0
    processed = 0
    for symbol, symbol_events in by_symbol.items():
        start = datetime.combine(scan['window_start_date'], datetime.min.time(), tzinfo=UTC) - timedelta(days=10)
        end = datetime.combine(scan['window_end_date_exclusive'], datetime.min.time(), tzinfo=UTC) + timedelta(
            minutes=int(scan['window_minutes'])
        )
        try:
            bars = client.klines(symbol, '15m', start, end)
            event_times = [event['baseline_time'] for event in symbol_events]
            used_control_times: list[datetime] = []
            for event in symbol_events:
                controls = select_controls(
                    bars,
                    event['baseline_time'],
                    [*event_times, *used_control_times],
                    int(job['controls_per_event']),
                    float(scan['threshold_pct']),
                    int(scan['window_minutes']),
                )
                used_control_times.extend(c['pseudo_baseline_time'] for c in controls)
                with connect(settings) as conn:
                    with conn.cursor() as cur:
                        for rank, control in enumerate(controls, start=1):
                            cur.execute(
                                """
                                insert into binance10_controls(
                                  control_job_id,event_id,symbol,pseudo_baseline_time,match_rank,match_score,
                                  match_basis,same_weekday,calendar_distance_days
                                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                on conflict (control_job_id,event_id,pseudo_baseline_time) do nothing
                                """,
                                (
                                    job_id, event['id'], symbol, control['pseudo_baseline_time'], rank,
                                    control['match_score'], control['match_basis'], control['same_weekday'],
                                    control['calendar_distance_days'],
                                ),
                            )
                        total_controls += len(controls)
                        processed += 1
                        cur.execute(
                            'update binance10_control_jobs set events_processed=%s, controls_created=%s, '
                            'heartbeat_at=now() where id=%s',
                            (processed, total_controls, job_id),
                        )
                    conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception('Control selection failed: %s', symbol)
            _record_issue(
                settings, job_id=job_id, job_kind='controls', symbol=symbol,
                stage='neutral_controls', error=exc,
            )
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update binance10_control_jobs set status=case when failures>0 then 'completed_with_warnings' "
                "else 'completed' end, completed_at=now() where id=%s",
                (job_id,),
            )
        conn.commit()


def _pages_for_minutes(minutes: int) -> int:
    return max(1, math.ceil(minutes / 1000))


def run_context_job(settings: Settings, job: dict) -> None:
    """Build raw point-in-time evidence; no predictor features are engineered."""
    client = BinanceClient(settings)
    job_id = str(job['id'])
    control_job = fetch_one(
        settings, 'select * from binance10_control_jobs where id=%s', (job['control_job_id'],)
    )
    scan = fetch_one(
        settings,
        'select s.* from binance10_scan_jobs s join binance10_control_jobs c on c.scan_job_id=s.id where c.id=%s',
        (job['control_job_id'],),
    )
    if not control_job or not scan:
        raise RuntimeError('Source jobs not found')
    events = fetch_all(
        settings,
        'select * from binance10_events where scan_job_id=%s and saleable=true order by baseline_time',
        (control_job['scan_job_id'],),
    )
    controls = fetch_all(
        settings,
        'select * from binance10_controls where control_job_id=%s order by event_id,match_rank',
        (job['control_job_id'],),
    )
    controls_by_event: dict[str, list[dict]] = {}
    for row in controls:
        controls_by_event.setdefault(str(row['event_id']), []).append(row)

    all_anchors = [event['baseline_time'] for event in events]
    all_anchors.extend(control['pseudo_baseline_time'] for control in controls)
    if not all_anchors:
        raise RuntimeError('No saleable events and controls are available for raw evidence export')

    metadata = {
        'protocol_version': 'binance10_v1_1_raw_evidence',
        'created_at': datetime.now(tz=UTC).isoformat(),
        'threshold_pct': float(scan['threshold_pct']),
        'window_minutes': scan['window_minutes'],
        'cooldown_minutes': scan['cooldown_minutes'],
        'subject_history': {
            '15m_days': SUBJECT_HISTORY_DAYS,
            '1m_hours': HIGH_RESOLUTION_HOURS,
        },
        'reference_context': list(REFERENCE_SYMBOLS.values()),
        'event_definition': (
            'First one-minute high reaching 10% above the lowest low in the preceding completed eight hours; '
            'one event per symbol per eight-hour cooldown.'
        ),
        'control_definition': (
            'Same symbol and same UTC 15-minute slot, excluding nearby threshold events; '
            'ranked by same weekday and then nearest eligible calendar date. No return, volatility, volume or other predictor matching.'
        ),
        'analysis_boundary': (
            'The app collects and quality-checks raw evidence only. It does not derive predictor features, identify patterns, '
            'fit models or create trading rules. ChatGPT performs those tasks after export.'
        ),
        'lookahead_protection': (
            'Every exported bar is fully closed before its sample anchor. The anchor minute itself is excluded.'
        ),
        'limitations': [
            'Initial scans use the current Binance Spot trading universe and can omit delisted historical symbols.',
            'Historical order-book queues are unavailable; saleability uses executed aggregate-trade notional with one-minute quote-volume fallback.',
            'Controls are observational and do not prove causal relationships.',
            'Raw one-minute context is limited to the final 48 hours to control export size; ten-day context is retained at 15-minute resolution.',
        ],
    }
    builder = RawEvidencePackageBuilder(settings, job_id, events, metadata)

    reference_15m: dict[str, list] = {}
    reference_1m: dict[str, list] = {}
    reference_start_15m = min(all_anchors) - timedelta(days=SUBJECT_HISTORY_DAYS, minutes=15)
    reference_start_1m = min(all_anchors) - timedelta(hours=HIGH_RESOLUTION_HOURS, minutes=1)
    reference_end = max(all_anchors)
    for ref_symbol in REFERENCE_SYMBOLS.values():
        try:
            reference_15m[ref_symbol] = client.klines(ref_symbol, '15m', reference_start_15m, reference_end)
            reference_1m[ref_symbol] = client.klines(ref_symbol, '1m', reference_start_1m, reference_end)
        except Exception as exc:  # noqa: BLE001
            reference_15m[ref_symbol] = []
            reference_1m[ref_symbol] = []
            _record_issue(
                settings, job_id=job_id, job_kind='context', symbol=ref_symbol,
                stage='raw_market_context', error=exc,
            )

    by_symbol: dict[str, list[dict]] = {}
    for event in events:
        by_symbol.setdefault(event['symbol'], []).append(event)

    processed_events = 0
    sample_count = 0
    raw_bar_rows = 0
    for symbol, symbol_events in by_symbol.items():
        symbol_samples: list[tuple[dict, dict | None]] = []
        for event in symbol_events:
            symbol_samples.append((event, None))
            for control in controls_by_event.get(str(event['id']), []):
                symbol_samples.append((event, control))
        anchors = [control['pseudo_baseline_time'] if control else event['baseline_time'] for event, control in symbol_samples]
        try:
            subject_15m_all = client.klines(
                symbol,
                '15m',
                min(anchors) - timedelta(days=SUBJECT_HISTORY_DAYS, minutes=15),
                max(anchors),
            )
            continuous_minutes = int(
                (max(anchors) - (min(anchors) - timedelta(hours=HIGH_RESOLUTION_HOURS, minutes=1))).total_seconds() // 60
            )
            use_continuous_1m = _pages_for_minutes(continuous_minutes) <= 3 * len(anchors)
            subject_1m_all = (
                client.klines(
                    symbol,
                    '1m',
                    min(anchors) - timedelta(hours=HIGH_RESOLUTION_HOURS, minutes=1),
                    max(anchors),
                )
                if use_continuous_1m else None
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception('Raw subject history failed: %s', symbol)
            _record_issue(
                settings, job_id=job_id, job_kind='context', symbol=symbol,
                stage='raw_subject_history', error=exc,
            )
            continue

        for event, control in symbol_samples:
            is_event = control is None
            anchor = event['baseline_time'] if is_event else control['pseudo_baseline_time']
            sample_id = f"event:{event['id']}" if is_event else f"control:{control['id']}"
            sample_kind = 'event' if is_event else 'control'
            sample = {
                'sample_id': sample_id,
                'event_id': str(event['id']),
                'control_id': None if is_event else str(control['id']),
                'symbol': symbol,
                'base_asset': event['base_asset'],
                'quote_asset': event['quote_asset'],
                'anchor_time': anchor.isoformat(),
                'sample_kind': sample_kind,
                'control_rank': None if is_event else control['match_rank'],
                'control_selection_basis': None if is_event else control.get('match_basis'),
            }
            outcome = {
                'sample_id': sample_id,
                'event_id': str(event['id']),
                'sample_kind': sample_kind,
                'did_10pct_event_occur': is_event,
                'crossing_time': event['crossing_time'].isoformat() if is_event else None,
                'minutes_to_cross': event['minutes_to_cross'] if is_event else None,
                'gain_pct': float(event['gain_pct']) if is_event else None,
                'exit_quote_notional': float(event['exit_quote_notional']) if is_event else None,
                'saleable': bool(event['saleable']) if is_event else False,
                'saleability_source': event['saleability_source'] if is_event else None,
            }
            subject_15m = completed_bars(
                subject_15m_all, anchor, timedelta(days=SUBJECT_HISTORY_DAYS)
            )
            try:
                subject_1m_source = subject_1m_all or client.klines(
                    symbol,
                    '1m',
                    anchor - timedelta(hours=HIGH_RESOLUTION_HOURS, minutes=1),
                    anchor,
                )
            except Exception as exc:  # noqa: BLE001
                subject_1m_source = []
                _record_issue(
                    settings, job_id=job_id, job_kind='context', symbol=symbol,
                    stage=f'raw_subject_1m:{sample_id}', error=exc,
                )
            subject_1m = completed_bars(
                subject_1m_source, anchor, timedelta(hours=HIGH_RESOLUTION_HOURS)
            )
            market_15m = {
                ref_symbol: completed_bars(
                    bars, anchor, timedelta(days=SUBJECT_HISTORY_DAYS)
                )
                for ref_symbol, bars in reference_15m.items()
            }
            market_1m = {
                ref_symbol: completed_bars(
                    bars, anchor, timedelta(hours=HIGH_RESOLUTION_HOURS)
                )
                for ref_symbol, bars in reference_1m.items()
            }
            raw_bar_rows += builder.add_sample(
                sample=sample,
                outcome=outcome,
                subject_15m=subject_15m,
                subject_1m=subject_1m,
                market_15m=market_15m,
                market_1m=market_1m,
            )
            sample_count += 1
            if is_event:
                processed_events += 1
                with connect(settings) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            'update binance10_context_jobs set events_processed=%s, samples_total=%s, '
                            'raw_bar_rows=%s, heartbeat_at=now() where id=%s',
                            (processed_events, sample_count, raw_bar_rows, job_id),
                        )
                    conn.commit()

    package_manifest = builder.finalise()
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update binance10_context_jobs set status=case when failures>0 then 'completed_with_warnings' "
                "else 'completed' end, completed_at=now(), result_json=%s, raw_bar_rows=%s where id=%s",
                (
                    json.dumps({
                        'packages': package_manifest,
                        'samples': sample_count,
                        'raw_bar_rows': raw_bar_rows,
                        'feature_rows': 0,
                    }),
                    raw_bar_rows,
                    job_id,
                ),
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
