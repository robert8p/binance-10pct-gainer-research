from __future__ import annotations

from bisect import bisect_left
import csv
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import uuid
from zipfile import ZIP_DEFLATED, ZipFile

from .config import Settings
from .db import connect
from .models import Kline
from .storage import upload, verify_upload

SUBJECTS_PER_SHARD = 8
SPLITS = ('discovery', 'validation', 'sealed_test')


def _sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_folder(folder: Path, output: Path) -> str:
    with ZipFile(output, 'w', ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(folder)))
    return _sha256_file(output)


def _delete_local(folder: Path, archive: Path) -> None:
    archive.unlink(missing_ok=True)
    shutil.rmtree(folder, ignore_errors=True)


def _register_upload(
    settings: Settings,
    export_job_id: str,
    local_path: Path,
    storage_path: str,
    digest: str,
    role: str,
    split: str | None,
) -> dict[str, object]:
    size = local_path.stat().st_size
    upload(settings, local_path, storage_path)
    verify_upload(settings, storage_path, size)
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into binance10_grid_files(
                  export_job_id,storage_path,filename,size_bytes,sha256,content_type,role,split
                ) values (%s,%s,%s,%s,%s,'application/zip',%s,%s)
                on conflict (export_job_id,storage_path) do update
                  set size_bytes=excluded.size_bytes,sha256=excluded.sha256,role=excluded.role,split=excluded.split
                """,
                (export_job_id, storage_path, local_path.name, size, digest, role, split),
            )
        conn.commit()
    return {
        'filename': local_path.name,
        'storage_path': storage_path,
        'size_bytes': size,
        'sha256': digest,
        'role': role,
        'split': split,
    }


def _create_subject_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute('pragma journal_mode=WAL')
    conn.execute('pragma synchronous=NORMAL')
    conn.execute('pragma temp_store=MEMORY')
    conn.executescript(
        """
        create table candidates (
          candidate_id text primary key, symbol text not null, base_asset text not null,
          quote_asset text not null, decision_time text not null, split text not null
        );
        create table outcomes (
          candidate_id text primary key, entry_price text not null,
          entry_quote_notional text not null, entry_trade_count integer not null,
          entry_liquid integer not null, target_price text not null,
          target_reached integer not null, crossing_minute text, minutes_to_cross integer,
          max_forward_high text not null, max_forward_gain_pct text not null,
          exit_quote_notional text not null, exit_trade_count integer not null,
          exit_liquid integer not null, liquidity_assessment_complete integer not null,
          actionable_10pct integer not null,
          label_version text not null
        );
        create table candidate_windows (
          candidate_id text not null, interval_minutes integer not null,
          window_start_time text not null, decision_time_exclusive text not null,
          primary key(candidate_id,interval_minutes)
        );
        create table bars (
          symbol text not null, interval_minutes integer not null,
          open_time text not null, close_time text not null,
          open text not null, high text not null, low text not null, close text not null,
          base_volume text not null, quote_volume text not null, trade_count integer not null,
          taker_buy_base_volume text not null, taker_buy_quote_volume text not null,
          primary key(symbol,interval_minutes,open_time)
        ) without rowid;
        create table candidate_quality (
          candidate_id text not null, interval_minutes integer not null,
          expected_bar_count integer not null, actual_bar_count integer not null,
          coverage_ratio real not null, complete_enough integer not null,
          primary key(candidate_id,interval_minutes)
        );
        create index idx_candidates_time on candidates(symbol,decision_time);
        create index idx_bars_lookup on bars(symbol,interval_minutes,open_time);
        create view candidate_bars as
        select c.candidate_id,c.symbol,w.interval_minutes,b.open_time,b.close_time,
               b.open,b.high,b.low,b.close,b.base_volume,b.quote_volume,b.trade_count,
               b.taker_buy_base_volume,b.taker_buy_quote_volume
          from candidates c
          join candidate_windows w on w.candidate_id=c.candidate_id
          join bars b on b.symbol=c.symbol and b.interval_minutes=w.interval_minutes
                     and b.open_time>=w.window_start_time
                     and b.close_time<w.decision_time_exclusive;
        """
    )
    return conn


def _create_ledger_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute('pragma journal_mode=WAL')
    conn.execute('pragma synchronous=NORMAL')
    conn.executescript(
        """
        create table candidates (
          candidate_id text primary key, symbol text not null, base_asset text not null,
          quote_asset text not null, decision_time text not null, split text not null
        );
        create table outcomes (
          candidate_id text primary key, entry_price text not null,
          entry_quote_notional text not null, entry_trade_count integer not null,
          entry_liquid integer not null, target_price text not null,
          target_reached integer not null, crossing_minute text, minutes_to_cross integer,
          max_forward_high text not null, max_forward_gain_pct text not null,
          exit_quote_notional text not null, exit_trade_count integer not null,
          exit_liquid integer not null, liquidity_assessment_complete integer not null,
          actionable_10pct integer not null,
          label_version text not null
        );
        create index idx_candidates_symbol_time on candidates(symbol,decision_time);
        create index idx_outcomes_label on outcomes(actionable_10pct,target_reached);
        """
    )
    return conn


def _create_market_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute('pragma journal_mode=WAL')
    conn.execute('pragma synchronous=NORMAL')
    conn.executescript(
        """
        create table decision_times (decision_time text primary key);
        create table market_windows (
          decision_time text not null, symbol text not null, interval_minutes integer not null,
          window_start_time text not null, decision_time_exclusive text not null,
          primary key(decision_time,symbol,interval_minutes)
        );
        create table bars (
          symbol text not null, interval_minutes integer not null,
          open_time text not null, close_time text not null,
          open text not null, high text not null, low text not null, close text not null,
          base_volume text not null, quote_volume text not null, trade_count integer not null,
          taker_buy_base_volume text not null, taker_buy_quote_volume text not null,
          primary key(symbol,interval_minutes,open_time)
        ) without rowid;
        create index idx_market_bars on bars(symbol,interval_minutes,open_time);
        create view market_decision_bars as
        select w.decision_time,w.symbol,w.interval_minutes,b.open_time,b.close_time,
               b.open,b.high,b.low,b.close,b.base_volume,b.quote_volume,b.trade_count,
               b.taker_buy_base_volume,b.taker_buy_quote_volume
          from market_windows w
          join bars b on b.symbol=w.symbol and b.interval_minutes=w.interval_minutes
                     and b.open_time>=w.window_start_time
                     and b.close_time<w.decision_time_exclusive;
        """
    )
    return conn


def _bar_tuple(symbol: str, interval: int, bar: Kline) -> tuple[object, ...]:
    return (
        symbol, interval, bar.open_time.isoformat(), bar.close_time.isoformat(),
        format(bar.open, 'f'), format(bar.high, 'f'), format(bar.low, 'f'), format(bar.close, 'f'),
        format(bar.volume, 'f'), format(bar.quote_volume, 'f'), bar.trades,
        format(bar.taker_buy_base, 'f'), format(bar.taker_buy_quote, 'f'),
    )


def _candidate_rows(row: dict) -> tuple[tuple[object, ...], tuple[object, ...]]:
    candidate = (
        str(row['id']), row['symbol'], row['base_asset'], row['quote_asset'],
        row['decision_time'].isoformat(), row['split'],
    )
    outcome = (
        str(row['id']), format(row['entry_price'], 'f'), format(row['entry_quote_notional'], 'f'),
        int(row['entry_trade_count']), int(bool(row['entry_liquid'])), format(row['target_price'], 'f'),
        int(bool(row['target_reached'])), row['crossing_minute'].isoformat() if row['crossing_minute'] else None,
        row['minutes_to_cross'], format(row['max_forward_high'], 'f'),
        format(row['max_forward_gain_pct'], 'f'), format(row['exit_quote_notional'], 'f'),
        int(row['exit_trade_count']), int(bool(row['exit_liquid'])), int(bool(row['liquidity_assessment_complete'])),
        int(bool(row['actionable_10pct'])), row['label_version'],
    )
    return candidate, outcome


def _window_count(times: list[datetime], start: datetime, end: datetime) -> int:
    return bisect_left(times, end) - bisect_left(times, start)


def _write_csv(conn: sqlite3.Connection, path: Path, query: str) -> None:
    cursor = conn.execute(query)
    names = [item[0] for item in cursor.description]
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(names)
        writer.writerows(cursor)


class GridEvidencePackageBuilder:
    def __init__(self, settings: Settings, export_job_id: str, metadata: dict[str, object]) -> None:
        self.settings = settings
        self.export_job_id = export_job_id
        self.attempt_id = uuid.uuid4().hex
        self.storage_prefix = f'grid-evidence/{export_job_id}/attempt_{self.attempt_id}'
        self.metadata = {**metadata, 'export_attempt_id': self.attempt_id, 'storage_prefix': self.storage_prefix}
        self.root = settings.temp_data_dir / 'grid_exports' / export_job_id
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.outputs: list[dict[str, object]] = []
        self.manifest: dict[str, list[dict[str, object]]] = {split: [] for split in SPLITS}

    def _package(self, folder: Path, filename: str, role: str, split: str | None) -> dict[str, object]:
        zip_path = self.root / filename
        digest = _zip_folder(folder, zip_path)
        record = _register_upload(
            self.settings, self.export_job_id, zip_path,
            f'{self.storage_prefix}/{filename}', digest, role, split,
        )
        self.outputs.append(record)
        if split:
            self.manifest[split].append(record)
        _delete_local(folder, zip_path)
        return record

    def add_ledger(self, split: str, candidates) -> dict[str, object]:
        folder = self.root / f'{split}_ledger'
        folder.mkdir(parents=True, exist_ok=True)
        conn = _create_ledger_db(folder / 'candidate_ledger.sqlite')
        count = target_count = actionable_count = 0
        symbols: set[str] = set()
        candidate_batch: list[tuple[object, ...]] = []
        outcome_batch: list[tuple[object, ...]] = []
        for row in candidates:
            candidate_row, outcome_row = _candidate_rows(row)
            candidate_batch.append(candidate_row)
            outcome_batch.append(outcome_row)
            count += 1
            target_count += int(bool(row['target_reached']))
            actionable_count += int(bool(row['actionable_10pct']))
            symbols.add(str(row['symbol']))
            if len(candidate_batch) >= 5000:
                conn.executemany('insert into candidates values (?,?,?,?,?,?)', candidate_batch)
                conn.executemany('insert into outcomes values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', outcome_batch)
                conn.commit()
                candidate_batch.clear()
                outcome_batch.clear()
        if candidate_batch:
            conn.executemany('insert into candidates values (?,?,?,?,?,?)', candidate_batch)
            conn.executemany('insert into outcomes values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', outcome_batch)
            conn.commit()
        _write_csv(conn, folder / 'candidates.csv', 'select * from candidates order by decision_time,symbol')
        _write_csv(conn, folder / 'outcomes.csv', 'select * from outcomes order by candidate_id')
        counts = {
            'candidates': count,
            'target_reached': target_count,
            'actionable_10pct': actionable_count,
            'symbols': len(symbols),
        }
        (folder / 'protocol.json').write_text(
            json.dumps({**self.metadata, 'split': split, 'package_role': 'complete_candidate_ledger', 'counts': counts}, indent=2, default=str),
            encoding='utf-8',
        )
        (folder / 'README.txt').write_text(
            'Complete candidate denominator for this chronological split. Outcomes are separated from predictor bars. '
            'Do not use outcome columns as predictors.\n', encoding='utf-8',
        )
        conn.close()
        return self._package(folder, f'binance10_{split}_ledger.zip', f'{split}_ledger', split)

    def add_market(
        self,
        split: str,
        decision_times: list[datetime],
        bars_by_symbol_interval: dict[tuple[str, int], list[Kline]],
        *,
        prior_days: int,
        high_res_hours: int,
    ) -> dict[str, object]:
        folder = self.root / f'{split}_market'
        folder.mkdir(parents=True, exist_ok=True)
        conn = _create_market_db(folder / 'market_context.sqlite')
        unique_times = sorted(set(decision_times))
        conn.executemany('insert into decision_times values (?)', [(value.isoformat(),) for value in unique_times])
        windows: list[tuple[object, ...]] = []
        for decision in unique_times:
            for symbol, interval in bars_by_symbol_interval:
                history = timedelta(days=prior_days) if interval == 15 else timedelta(hours=high_res_hours)
                windows.append((decision.isoformat(), symbol, interval, (decision - history).isoformat(), decision.isoformat()))
        conn.executemany('insert into market_windows values (?,?,?,?,?)', windows)
        rows = []
        for (symbol, interval), bars in bars_by_symbol_interval.items():
            rows.extend(_bar_tuple(symbol, interval, bar) for bar in bars)
        conn.executemany('insert or ignore into bars values (?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        conn.commit()
        counts = {
            'unique_decision_times': len(unique_times),
            'unique_bar_rows': int(conn.execute('select count(*) from bars').fetchone()[0]),
            'symbols': sorted({key[0] for key in bars_by_symbol_interval}),
        }
        (folder / 'protocol.json').write_text(
            json.dumps({**self.metadata, 'split': split, 'package_role': 'market_context', 'counts': counts}, indent=2, default=str),
            encoding='utf-8',
        )
        (folder / 'README.txt').write_text(
            'BTC, ETH and BNB raw context stored once. Use market_decision_bars for strict pre-decision windows.\n',
            encoding='utf-8',
        )
        conn.close()
        return self._package(folder, f'binance10_{split}_market.zip', f'{split}_market', split)

    def add_subject_shard(
        self,
        split: str,
        part: int,
        candidates: list[dict],
        bars_by_symbol_interval: dict[tuple[str, int], list[Kline]],
        *,
        prior_days: int,
        high_res_hours: int,
    ) -> dict[str, object]:
        folder = self.root / f'{split}_subject_{part:03d}'
        folder.mkdir(parents=True, exist_ok=True)
        conn = _create_subject_db(folder / 'subject_evidence.sqlite')
        candidate_rows, outcome_rows = zip(*(_candidate_rows(row) for row in candidates)) if candidates else ([], [])
        conn.executemany('insert into candidates values (?,?,?,?,?,?)', candidate_rows)
        conn.executemany('insert into outcomes values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', outcome_rows)
        bars_times: dict[tuple[str, int], list[datetime]] = {}
        bar_rows: list[tuple[object, ...]] = []
        for key, bars in bars_by_symbol_interval.items():
            bars_times[key] = [bar.open_time for bar in bars]
            bar_rows.extend(_bar_tuple(key[0], key[1], bar) for bar in bars)
        conn.executemany('insert or ignore into bars values (?,?,?,?,?,?,?,?,?,?,?,?,?)', bar_rows)
        windows: list[tuple[object, ...]] = []
        quality: list[tuple[object, ...]] = []
        for row in candidates:
            candidate_id = str(row['id'])
            decision = row['decision_time']
            symbol = row['symbol']
            for interval, history, expected in (
                (15, timedelta(days=prior_days), prior_days * 24 * 4),
                (1, timedelta(hours=high_res_hours), high_res_hours * 60),
            ):
                start = decision - history
                windows.append((candidate_id, interval, start.isoformat(), decision.isoformat()))
                actual = _window_count(bars_times.get((symbol, interval), []), start, decision)
                ratio = actual / expected if expected else 0.0
                quality.append((candidate_id, interval, expected, actual, round(ratio, 6), int(ratio >= 0.98)))
        conn.executemany('insert into candidate_windows values (?,?,?,?)', windows)
        conn.executemany('insert into candidate_quality values (?,?,?,?,?,?)', quality)
        conn.commit()
        _write_csv(conn, folder / 'candidates.csv', 'select * from candidates order by decision_time,symbol')
        _write_csv(conn, folder / 'outcomes.csv', 'select * from outcomes order by candidate_id')
        _write_csv(conn, folder / 'candidate_quality.csv', 'select * from candidate_quality order by candidate_id,interval_minutes')
        counts = {
            'candidates': len(candidates),
            'symbols': sorted({row['symbol'] for row in candidates}),
            'unique_bar_rows': int(conn.execute('select count(*) from bars').fetchone()[0]),
            'complete_15m_candidates': int(conn.execute('select count(*) from candidate_quality where interval_minutes=15 and complete_enough=1').fetchone()[0]),
            'complete_1m_candidates': int(conn.execute('select count(*) from candidate_quality where interval_minutes=1 and complete_enough=1').fetchone()[0]),
        }
        (folder / 'protocol.json').write_text(
            json.dumps({**self.metadata, 'split': split, 'part': part, 'package_role': 'subject_raw_evidence', 'counts': counts}, indent=2, default=str),
            encoding='utf-8',
        )
        (folder / 'CHATGPT_ANALYSIS_PROTOCOL.md').write_text(_analysis_protocol(split), encoding='utf-8')
        conn.close()
        filename = (
            f'SEALED_TEST_DO_NOT_OPEN_subject_part_{part:03d}.zip'
            if split == 'sealed_test'
            else f'binance10_{split}_subject_part_{part:03d}.zip'
        )
        return self._package(folder, filename, f'{split}_subject_part_{part:03d}', split)

    def finalise(self, split_counts: dict[str, dict[str, object]]) -> list[dict[str, object]]:
        folder = self.root / 'index'
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            **self.metadata,
            'protocol_version': 'binance10_v1_2_executable_grid',
            'subject_symbols_per_shard': SUBJECTS_PER_SHARD,
            'split_counts': split_counts,
            'files': self.manifest,
            'discovery_upload_order': [
                item['filename'] for item in self.manifest['discovery']
            ],
            'instructions': {
                'discovery': 'Upload the index and every discovery file only.',
                'validation': 'Do not open until rules and acceptance criteria are frozen.',
                'sealed_test': 'Do not open until validation passes without retuning.',
                'analysis_owner': 'ChatGPT derives all predictor features and identifies patterns.',
            },
        }
        (folder / 'manifest.json').write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
        (folder / 'README.txt').write_text(
            'The candidate ledger contains every eligible 15-minute decision in each split. '
            'The app has not selected controls or engineered predictor features.\n', encoding='utf-8',
        )
        (folder / 'CHATGPT_ANALYSIS_PROTOCOL.md').write_text(_analysis_protocol('index'), encoding='utf-8')
        record = self._package(folder, 'binance10_index.zip', 'index', None)
        self.outputs.insert(0, self.outputs.pop())
        try:
            self.root.rmdir()
        except OSError:
            pass
        return self.outputs


def _analysis_protocol(split: str) -> str:
    if split == 'sealed_test':
        return '# Sealed test\n\nDo not inspect until validation passes without rule changes.\n'
    return (
        '# ChatGPT-owned pattern discovery\n\n'
        'The app generated one identical 15-minute decision grid for positives and negatives. '
        'It used the interval open as the first executable trade-price benchmark and labelled whether +10% was reached within eight hours.\n\n'
        'Use `candidate_bars` only for subject predictors and `market_decision_bars` for market context; both end strictly before the decision time. '
        'Never use `outcomes` as predictor inputs. Assess data quality first. Derive features from raw sequences, report all candidate families tested, '
        'control for repeated symbols/times and multiple testing, and calculate weighted or full-population precision, alerts per day, no-trade frequency, '
        'fees and slippage. Freeze exact rules before validation.\n'
    )
