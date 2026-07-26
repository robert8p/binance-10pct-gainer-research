from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import traceback
from typing import Iterator

from .binance import BinanceClient
from .config import Settings
from .db import connect, fetch_all, fetch_one
from .exporter import GridEvidencePackageBuilder, SPLITS, SUBJECTS_PER_SHARD
from .grid import candidate_times, coarse_positive_times, evaluate_candidate, overlapping_groups
from .protocol import assign_split, split_boundaries
from .storage import delete_prefix

logger = logging.getLogger(__name__)
UTC = timezone.utc
REFERENCE_SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'BNBUSDT')


def recover_interrupted_jobs(settings: Settings) -> None:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update binance10_grid_jobs set status='queued',started_at=null,heartbeat_at=null "
                "where status='running'"
            )
            cur.execute("select id from binance10_export_jobs where status='running'")
            export_ids = [str(row['id']) for row in cur.fetchall()]
            for job_id in export_ids:
                cur.execute('delete from binance10_grid_files where export_job_id=%s', (job_id,))
                cur.execute('delete from binance10_grid_issues where export_job_id=%s', (job_id,))
                cur.execute(
                    "update binance10_export_jobs set status='queued',started_at=null,completed_at=null,heartbeat_at=null,"
                    "symbols_processed=0,files_created=0,raw_bar_rows=0,failures=0,result_json=null,error_message=null "
                    "where id=%s",
                    (job_id,),
                )
        conn.commit()


def _claim(settings: Settings, table: str) -> dict | None:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select * from {table} where status='queued' order by created_at for update skip locked limit 1"
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    f"update {table} set status='running',started_at=coalesce(started_at,now()),heartbeat_at=now() where id=%s",
                    (row['id'],),
                )
        conn.commit()
    return dict(row) if row else None


def _fail(settings: Settings, table: str, job_id: str, error: Exception) -> None:
    message = f'{type(error).__name__}: {error}\n{traceback.format_exc()}'[:12000]
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update {table} set status='failed',completed_at=now(),heartbeat_at=now(),error_message=%s where id=%s",
                (message, job_id),
            )
        conn.commit()


def _record_issue(
    settings: Settings,
    *,
    grid_job_id: str | None,
    export_job_id: str | None,
    symbol: str | None,
    stage: str,
    error: Exception,
) -> None:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into binance10_grid_issues(grid_job_id,export_job_id,symbol,stage,message) values (%s,%s,%s,%s,%s)",
                (grid_job_id, export_job_id, symbol, stage, str(error)[:4000]),
            )
            if grid_job_id:
                cur.execute('update binance10_grid_jobs set failures=failures+1 where id=%s', (grid_job_id,))
            if export_job_id:
                cur.execute('update binance10_export_jobs set failures=failures+1 where id=%s', (export_job_id,))
        conn.commit()


def _candidate_insert_rows(
    grid_job_id: str,
    symbol_row: dict[str, str],
    outcomes,
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for outcome in outcomes:
        candidate_key = f"{symbol_row['symbol']}:{outcome.decision_time.isoformat()}"
        rows.append((
            grid_job_id, candidate_key, symbol_row['symbol'], symbol_row['base_asset'], symbol_row['quote_asset'],
            outcome.decision_time, outcome.split, outcome.entry_price, outcome.entry_quote_notional,
            outcome.entry_trade_count, outcome.entry_liquid, outcome.target_price, outcome.target_reached,
            outcome.crossing_minute, outcome.minutes_to_cross, outcome.max_forward_high,
            outcome.max_forward_gain_pct, outcome.exit_quote_notional, outcome.exit_trade_count,
            outcome.exit_liquid, outcome.liquidity_assessment_complete, outcome.actionable_10pct, 'binance10_v1_2_executable_grid',
        ))
    return rows


def run_grid_job(settings: Settings, job: dict) -> None:
    client = BinanceClient(settings)
    job_id = str(job['id'])
    threshold_pct = Decimal(str(job['threshold_pct']))
    cadence_minutes = int(job['cadence_minutes'])
    horizon_minutes = int(job['horizon_minutes'])
    entry_liquidity_minutes = int(job['entry_liquidity_minutes'])
    exit_liquidity_minutes = int(job['exit_liquidity_minutes'])
    min_entry_notional = Decimal(str(job['min_entry_notional']))
    min_exit_notional = Decimal(str(job['min_exit_notional']))
    start = datetime.combine(job['window_start_date'], datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(job['window_end_date_exclusive'], datetime.min.time(), tzinfo=UTC)
    boundaries = split_boundaries(
        start, end, cadence_minutes=cadence_minutes, horizon_minutes=horizon_minutes
    )
    symbols = client.active_spot_symbols(job['quote_assets'])
    if settings.max_symbols > 0:
        symbols = symbols[:settings.max_symbols]
    resume_from = min(int(job.get('symbols_processed') or 0), len(symbols))
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update binance10_grid_jobs set symbols_total=%s,split_boundaries_json=%s where id=%s",
                (len(symbols), json.dumps({
                    'discovery_start': boundaries.discovery_start.isoformat(),
                    'discovery_end': boundaries.discovery_end.isoformat(),
                    'validation_start': boundaries.validation_start.isoformat(),
                    'validation_end': boundaries.validation_end.isoformat(),
                    'sealed_start': boundaries.sealed_start.isoformat(),
                    'sealed_end': boundaries.sealed_end.isoformat(),
                    'embargo_minutes': boundaries.embargo_minutes,
                }), job_id),
            )
        conn.commit()

    for index, symbol_row in enumerate(symbols[resume_from:], start=resume_from + 1):
        symbol = symbol_row['symbol']
        try:
            bars_15m = client.klines(
                symbol, '15m', start, end + timedelta(minutes=horizon_minutes + exit_liquidity_minutes + 15)
            )
            decisions = candidate_times(
                bars_15m, start, end, boundaries,
                horizon_minutes=horizon_minutes, cadence_minutes=cadence_minutes,
            )
            coarse_positives = coarse_positive_times(
                bars_15m, decisions, threshold_pct=threshold_pct, horizon_minutes=horizon_minutes
            )
            one_minute_groups: list[tuple[datetime, datetime, list]] = []
            for group_start, group_end in overlapping_groups(sorted(coarse_positives), horizon_minutes):
                fetch_end = group_end + timedelta(minutes=exit_liquidity_minutes + 2)
                one_minute_groups.append((
                    group_start,
                    fetch_end,
                    client.klines(symbol, '1m', group_start, fetch_end),
                ))

            times_15m = [bar.open_time for bar in bars_15m]
            index_by_time = {value: idx for idx, value in enumerate(times_15m)}
            outcomes = []
            bars_per_horizon = horizon_minutes // cadence_minutes
            for decision in decisions:
                idx = index_by_time[decision]
                local_15m = bars_15m[idx:idx + bars_per_horizon]
                local_1m = None
                if decision in coarse_positives:
                    for group_start, group_end, group_bars in one_minute_groups:
                        if group_start <= decision and decision + timedelta(minutes=horizon_minutes) < group_end:
                            local_1m = group_bars
                            break
                outcomes.append(evaluate_candidate(
                    decision,
                    assign_split(decision, boundaries),
                    local_15m,
                    local_1m,
                    threshold_pct=threshold_pct,
                    horizon_minutes=horizon_minutes,
                    entry_liquidity_minutes=entry_liquidity_minutes,
                    exit_liquidity_minutes=exit_liquidity_minutes,
                    min_entry_notional=min_entry_notional,
                    min_exit_notional=min_exit_notional,
                ))

            rows = _candidate_insert_rows(job_id, symbol_row, outcomes)
            with connect(settings) as conn:
                with conn.cursor() as cur:
                    if rows:
                        cur.executemany(
                            """
                            insert into binance10_candidates(
                              grid_job_id,candidate_key,symbol,base_asset,quote_asset,decision_time,split,
                              entry_price,entry_quote_notional,entry_trade_count,entry_liquid,target_price,
                              target_reached,crossing_minute,minutes_to_cross,max_forward_high,max_forward_gain_pct,
                              exit_quote_notional,exit_trade_count,exit_liquid,liquidity_assessment_complete,actionable_10pct,label_version
                            ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            on conflict (grid_job_id,candidate_key) do update set
                              split=excluded.split,entry_price=excluded.entry_price,
                              entry_quote_notional=excluded.entry_quote_notional,
                              entry_trade_count=excluded.entry_trade_count,entry_liquid=excluded.entry_liquid,
                              target_price=excluded.target_price,target_reached=excluded.target_reached,
                              crossing_minute=excluded.crossing_minute,minutes_to_cross=excluded.minutes_to_cross,
                              max_forward_high=excluded.max_forward_high,
                              max_forward_gain_pct=excluded.max_forward_gain_pct,
                              exit_quote_notional=excluded.exit_quote_notional,
                              exit_trade_count=excluded.exit_trade_count,exit_liquid=excluded.exit_liquid,
                              liquidity_assessment_complete=excluded.liquidity_assessment_complete,
                              actionable_10pct=excluded.actionable_10pct,label_version=excluded.label_version
                            """,
                            rows,
                        )
                    cur.execute(
                        """
                        select count(*) as total,
                               count(*) filter (where target_reached) as target_count,
                               count(*) filter (where actionable_10pct) as actionable_count
                          from binance10_candidates where grid_job_id=%s
                        """,
                        (job_id,),
                    )
                    counts = cur.fetchone()
                    cur.execute(
                        "update binance10_grid_jobs set symbols_processed=%s,candidates_total=%s,"
                        "target_reached_count=%s,actionable_count=%s,heartbeat_at=now() where id=%s",
                        (index, counts['total'], counts['target_count'], counts['actionable_count'], job_id),
                    )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception('Grid evaluation failed for %s', symbol)
            _record_issue(
                settings, grid_job_id=job_id, export_job_id=None, symbol=symbol,
                stage='candidate_grid', error=exc,
            )
            with connect(settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        'update binance10_grid_jobs set symbols_processed=%s,heartbeat_at=now() where id=%s',
                        (index, job_id),
                    )
                conn.commit()

    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select split,count(*) as candidates,
                       count(*) filter (where target_reached) as target_reached,
                       count(*) filter (where actionable_10pct) as actionable
                  from binance10_candidates where grid_job_id=%s group by split order by split
                """,
                (job_id,),
            )
            split_counts = [dict(row) for row in cur.fetchall()]
            cur.execute(
                "update binance10_grid_jobs set status=case when failures>0 then 'completed_with_warnings' else 'completed' end,"
                "completed_at=now(),heartbeat_at=now(),result_json=%s where id=%s",
                (json.dumps({'split_counts': split_counts}), job_id),
            )
        conn.commit()


def _candidate_stream(settings: Settings, grid_job_id: str, split: str) -> Iterator[dict]:
    with connect(settings) as conn:
        with conn.cursor(name=f'ledger_{split}_{grid_job_id.replace("-", "")[:12]}') as cur:
            cur.execute(
                "select * from binance10_candidates where grid_job_id=%s and split=%s order by decision_time,symbol",
                (grid_job_id, split),
            )
            while True:
                rows = cur.fetchmany(5000)
                if not rows:
                    break
                for row in rows:
                    yield dict(row)


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def run_export_job(settings: Settings, job: dict) -> None:
    client = BinanceClient(settings)
    job_id = str(job['id'])
    grid_job_id = str(job['grid_job_id'])
    grid_job = fetch_one(settings, 'select * from binance10_grid_jobs where id=%s', (grid_job_id,))
    if not grid_job:
        raise RuntimeError('Source grid job not found')
    prior_days = int(job['prior_days'])
    high_res_hours = int(job['high_res_hours'])

    stale_prefix = f'grid-evidence/{job_id}/'
    deleted = delete_prefix(settings, stale_prefix)
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute('delete from binance10_grid_files where export_job_id=%s', (job_id,))
            cur.execute('delete from binance10_grid_issues where export_job_id=%s', (job_id,))
            cur.execute('update binance10_export_jobs set heartbeat_at=now() where id=%s', (job_id,))
        conn.commit()
    logger.info('Cleaned %s stale export object(s) for %s', len(deleted), job_id)

    metadata = {
        'created_at': datetime.now(tz=UTC).isoformat(),
        'source_grid_job_id': grid_job_id,
        'decision_grid': 'Every eligible Binance Spot symbol at every 15-minute UTC boundary.',
        'entry_definition': (
            'The 15-minute kline open: Binance first trade price after the decision boundary; '
            'candidates with zero trades are excluded.'
        ),
        'outcome_definition': (
            'Price reaches +10% from entry within eight hours. Primary actionable label additionally requires '
            'minimum quote notional in the entry 15-minute bar and the next five full one-minute bars after the crossing minute.'
        ),
        'split_definition': (
            'Chronological 60/20/20 split created before analysis, with an eight-hour embargo before validation and sealed-test boundaries.'
        ),
        'predictor_history': {'15m_days': prior_days, '1m_hours': high_res_hours},
        'analysis_boundary': 'The app labels the complete candidate population and exports raw bars. ChatGPT derives predictor features and patterns.',
        'storage_cleanup_deleted_objects': len(deleted),
        'limitations': [
            'The current Binance Spot universe can omit pairs that were delisted before the scan date.',
            'Kline open is the first trade price in the interval but does not expose the exact sub-minute timestamp.',
            'Entry/exit quote volume is a liquidity screen, not a reconstruction of historical order-book queue position or guaranteed fill price.',
        ],
    }
    builder = GridEvidencePackageBuilder(settings, job_id, metadata)
    split_counts: dict[str, dict[str, object]] = {}
    files_created = 0
    symbols_processed = 0
    raw_bar_rows = 0

    for split in SPLITS:
        summary = fetch_one(
            settings,
            """
            select count(*) as candidates,count(*) filter (where target_reached) as target_reached,
                   count(*) filter (where actionable_10pct) as actionable,
                   count(distinct symbol) as symbols,min(decision_time) as min_time,max(decision_time) as max_time
              from binance10_candidates where grid_job_id=%s and split=%s
            """,
            (grid_job_id, split),
        )
        if not summary or int(summary['candidates']) == 0:
            split_counts[split] = {'candidates': 0, 'files': 0}
            continue
        split_counts[split] = {
            'candidates': int(summary['candidates']),
            'target_reached': int(summary['target_reached']),
            'actionable_10pct': int(summary['actionable']),
            'symbols': int(summary['symbols']),
            'start': summary['min_time'].isoformat(),
            'end': summary['max_time'].isoformat(),
        }

        builder.add_ledger(split, _candidate_stream(settings, grid_job_id, split))
        files_created += 1

        decision_rows = fetch_all(
            settings,
            'select distinct decision_time from binance10_candidates where grid_job_id=%s and split=%s order by decision_time',
            (grid_job_id, split),
        )
        decision_times = [row['decision_time'] for row in decision_rows]
        min_time = decision_times[0]
        max_time = decision_times[-1]
        market_bars: dict[tuple[str, int], list] = {}
        for ref_symbol in REFERENCE_SYMBOLS:
            market_bars[(ref_symbol, 15)] = client.klines(
                ref_symbol, '15m', min_time - timedelta(days=prior_days, minutes=15), max_time
            )
            market_bars[(ref_symbol, 1)] = client.klines(
                ref_symbol, '1m', min_time - timedelta(hours=high_res_hours, minutes=1), max_time
            )
        raw_bar_rows += sum(len(value) for value in market_bars.values())
        builder.add_market(
            split, decision_times, market_bars,
            prior_days=prior_days, high_res_hours=high_res_hours,
        )
        files_created += 1

        symbol_rows = fetch_all(
            settings,
            'select distinct symbol from binance10_candidates where grid_job_id=%s and split=%s order by symbol',
            (grid_job_id, split),
        )
        symbols = [row['symbol'] for row in symbol_rows]
        for part, symbol_group in enumerate(_chunks(symbols, SUBJECTS_PER_SHARD), start=1):
            candidates = fetch_all(
                settings,
                'select * from binance10_candidates where grid_job_id=%s and split=%s and symbol=any(%s) order by decision_time,symbol',
                (grid_job_id, split, symbol_group),
            )
            bars_by_key: dict[tuple[str, int], list] = {}
            for symbol in symbol_group:
                symbol_candidates = [row for row in candidates if row['symbol'] == symbol]
                symbol_min = min(row['decision_time'] for row in symbol_candidates)
                symbol_max = max(row['decision_time'] for row in symbol_candidates)
                try:
                    bars_by_key[(symbol, 15)] = client.klines(
                        symbol, '15m', symbol_min - timedelta(days=prior_days, minutes=15), symbol_max
                    )
                    bars_by_key[(symbol, 1)] = client.klines(
                        symbol, '1m', symbol_min - timedelta(hours=high_res_hours, minutes=1), symbol_max
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception('Raw evidence failed for %s', symbol)
                    bars_by_key[(symbol, 15)] = []
                    bars_by_key[(symbol, 1)] = []
                    _record_issue(
                        settings, grid_job_id=None, export_job_id=job_id, symbol=symbol,
                        stage=f'raw_evidence_{split}', error=exc,
                    )
                symbols_processed += 1
            raw_bar_rows += sum(len(value) for value in bars_by_key.values())
            builder.add_subject_shard(
                split, part, candidates, bars_by_key,
                prior_days=prior_days, high_res_hours=high_res_hours,
            )
            files_created += 1
            with connect(settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        'update binance10_export_jobs set symbols_processed=%s,files_created=%s,raw_bar_rows=%s,heartbeat_at=now() where id=%s',
                        (symbols_processed, files_created, raw_bar_rows, job_id),
                    )
                conn.commit()

    outputs = builder.finalise(split_counts)
    files_created = len(outputs)
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update binance10_export_jobs set status=case when failures>0 then 'completed_with_warnings' else 'completed' end,"
                "completed_at=now(),heartbeat_at=now(),files_created=%s,raw_bar_rows=%s,result_json=%s where id=%s",
                (files_created, raw_bar_rows, json.dumps({'packages': outputs, 'split_counts': split_counts}), job_id),
            )
        conn.commit()


def process_one(settings: Settings) -> bool:
    for table, runner in (
        ('binance10_grid_jobs', run_grid_job),
        ('binance10_export_jobs', run_export_job),
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
