from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import shutil
from zipfile import ZIP_DEFLATED, ZipFile

from .config import Settings
from .db import connect
from .models import Kline
from .raw_evidence import decimal_text, quality_record
from .storage import upload

UTC = timezone.utc
EVENT_GROUPS_PER_SHARD = 50

SAMPLE_FIELDS = [
    'sample_id', 'event_id', 'control_id', 'symbol', 'base_asset', 'quote_asset',
    'anchor_time', 'sample_kind', 'control_rank', 'control_selection_basis',
]
OUTCOME_FIELDS = [
    'sample_id', 'event_id', 'sample_kind', 'did_10pct_event_occur',
    'crossing_time', 'minutes_to_cross', 'gain_pct', 'exit_quote_notional',
    'saleable', 'saleability_source',
]
WINDOW_FIELDS = [
    'sample_id', 'source_role', 'source_symbol', 'interval_minutes',
    'window_start_time', 'anchor_time_exclusive',
]
QUALITY_FIELDS = [
    'sample_id', 'label', 'event_id', 'source_role', 'source_symbol',
    'interval_minutes', 'target_minutes', 'target_bar_count', 'actual_bar_count',
    'coverage_ratio', 'first_open_time', 'last_close_time', 'gap_count',
    'duplicate_count', 'non_monotonic_count', 'complete_enough',
]


def event_splits(events: list[dict[str, object]]) -> dict[str, list[str]]:
    ordered = sorted(events, key=lambda row: str(row.get('baseline_time', '')))
    event_ids = [str(row['id']) for row in ordered]
    n = len(event_ids)
    if n < 5:
        return {'discovery': event_ids, 'validation': [], 'sealed_test': []}
    discovery_n = max(1, math.floor(n * 0.60))
    validation_n = max(1, math.floor(n * 0.20))
    if discovery_n + validation_n >= n:
        validation_n = 1
        discovery_n = n - 2
    return {
        'discovery': event_ids[:discovery_n],
        'validation': event_ids[discovery_n:discovery_n + validation_n],
        'sealed_test': event_ids[discovery_n + validation_n:],
    }


# Backwards-compatible helper retained for tests and old callers.
def _event_splits(sample_rows: list[dict[str, object]]) -> dict[str, set[str]]:
    events = [
        {'id': row['event_id'], 'baseline_time': row.get('anchor_time')}
        for row in sample_rows if row.get('label') == 'event'
    ]
    return {key: set(value) for key, value in event_splits(events).items()}


def _zip_folder(folder: Path, output: Path) -> str:
    with ZipFile(output, 'w', ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(folder)))
    return hashlib.sha256(output.read_bytes()).hexdigest()


def _register_upload(
    settings: Settings,
    context_job_id: str,
    local_path: Path,
    storage_path: str,
    digest: str,
    role: str,
) -> dict[str, object]:
    upload(settings, local_path, storage_path)
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into binance10_files(context_job_id, storage_path, filename, size_bytes, sha256, content_type, role)
                values (%s,%s,%s,%s,%s,'application/zip',%s)
                on conflict (context_job_id, storage_path) do update
                  set size_bytes=excluded.size_bytes, sha256=excluded.sha256, role=excluded.role
                """,
                (context_job_id, storage_path, local_path.name, local_path.stat().st_size, digest, role),
            )
        conn.commit()
    return {
        'role': role,
        'filename': local_path.name,
        'storage_path': storage_path,
        'size_bytes': local_path.stat().st_size,
        'sha256': digest,
    }


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)] or [[]]


def _create_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute('pragma journal_mode=WAL')
    conn.execute('pragma synchronous=NORMAL')
    conn.execute('pragma temp_store=MEMORY')
    conn.executescript(
        """
        create table samples (
          sample_id text primary key, event_id text not null, control_id text,
          symbol text not null, base_asset text not null, quote_asset text not null,
          anchor_time text not null, sample_kind text not null,
          control_rank integer, control_selection_basis text
        );
        create table outcomes (
          sample_id text primary key, event_id text not null, sample_kind text not null,
          did_10pct_event_occur integer not null, crossing_time text, minutes_to_cross integer,
          gain_pct real, exit_quote_notional real, saleable integer not null, saleability_source text
        );
        create table sample_windows (
          sample_id text not null, source_role text not null, source_symbol text not null,
          interval_minutes integer not null, window_start_time text not null,
          anchor_time_exclusive text not null,
          primary key(sample_id, source_role, source_symbol, interval_minutes)
        );
        create table bars (
          source_symbol text not null, interval_minutes integer not null,
          open_time text not null, close_time text not null,
          open text not null, high text not null, low text not null, close text not null,
          base_volume text not null, quote_volume text not null, trade_count integer not null,
          taker_buy_base_volume text not null, taker_buy_quote_volume text not null,
          primary key(source_symbol, interval_minutes, open_time)
        ) without rowid;
        create table quality (
          sample_id text not null, label text not null, event_id text not null,
          source_role text not null, source_symbol text not null, interval_minutes integer not null,
          target_minutes integer not null, target_bar_count integer not null,
          actual_bar_count integer not null, coverage_ratio real not null,
          first_open_time text, last_close_time text, gap_count integer not null,
          duplicate_count integer not null, non_monotonic_count integer not null,
          complete_enough integer not null,
          primary key(sample_id, source_role, source_symbol, interval_minutes)
        );
        create index idx_windows_sample on sample_windows(sample_id);
        create index idx_bars_lookup on bars(source_symbol, interval_minutes, open_time);
        create index idx_quality_sample on quality(sample_id);
        create view sample_bars as
        select
          w.sample_id, w.source_role, w.source_symbol, w.interval_minutes,
          b.open_time, b.close_time, b.open, b.high, b.low, b.close,
          b.base_volume, b.quote_volume, b.trade_count,
          b.taker_buy_base_volume, b.taker_buy_quote_volume
        from sample_windows w
        join bars b
          on b.source_symbol = w.source_symbol
         and b.interval_minutes = w.interval_minutes
         and b.open_time >= w.window_start_time
         and b.close_time < w.anchor_time_exclusive;
        """
    )
    return conn


def _write_query_csv(conn: sqlite3.Connection, path: Path, query: str, fields: list[str]) -> None:
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for row in conn.execute(query):
            writer.writerow(row)


class _Shard:
    def __init__(self, folder: Path, split: str, part: int, event_ids: list[str]) -> None:
        self.folder = folder
        self.split = split
        self.part = part
        self.event_ids = set(event_ids)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db_path = self.folder / 'raw_evidence.sqlite'
        self.conn = _create_sqlite(self.db_path)
        self.sample_count = 0
        self.bar_reference_count = 0
        self.quality_count = 0

    def add_sample(
        self,
        *,
        sample: dict[str, object],
        outcome: dict[str, object],
        subject_15m: list[Kline],
        subject_1m: list[Kline],
        market_15m: dict[str, list[Kline]],
        market_1m: dict[str, list[Kline]],
    ) -> int:
        self.conn.execute(
            'insert into samples values (?,?,?,?,?,?,?,?,?,?)',
            tuple(sample.get(field) for field in SAMPLE_FIELDS),
        )
        self.conn.execute(
            'insert into outcomes values (?,?,?,?,?,?,?,?,?,?)',
            (
                outcome.get('sample_id'), outcome.get('event_id'), outcome.get('sample_kind'),
                int(bool(outcome.get('did_10pct_event_occur'))), outcome.get('crossing_time'),
                outcome.get('minutes_to_cross'), outcome.get('gain_pct'),
                outcome.get('exit_quote_notional'), int(bool(outcome.get('saleable'))),
                outcome.get('saleability_source'),
            ),
        )
        self.sample_count += 1
        sample_id = str(sample['sample_id'])
        event_id = str(sample['event_id'])
        label = str(sample['sample_kind'])
        anchor = str(sample['anchor_time'])
        linked_rows = 0

        datasets = [
            ('subject', str(sample['symbol']), 15, 10 * 24 * 60, subject_15m),
            ('subject', str(sample['symbol']), 1, 48 * 60, subject_1m),
            *[('market_reference', symbol, 15, 10 * 24 * 60, bars) for symbol, bars in market_15m.items()],
            *[('market_reference', symbol, 1, 48 * 60, bars) for symbol, bars in market_1m.items()],
        ]
        for source_role, source_symbol, interval, target_minutes, bars in datasets:
            window_start = (
                datetime.fromisoformat(anchor) - timedelta(minutes=target_minutes)
            ).isoformat()
            self.conn.execute(
                'insert into sample_windows values (?,?,?,?,?,?)',
                (sample_id, source_role, source_symbol, interval, window_start, anchor),
            )
            self.conn.executemany(
                'insert or ignore into bars values (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                [
                    (
                        source_symbol, interval, bar.open_time.isoformat(), bar.close_time.isoformat(),
                        decimal_text(bar.open), decimal_text(bar.high), decimal_text(bar.low),
                        decimal_text(bar.close), decimal_text(bar.volume), decimal_text(bar.quote_volume),
                        bar.trades, decimal_text(bar.taker_buy_base), decimal_text(bar.taker_buy_quote),
                    )
                    for bar in bars
                ],
            )
            q = quality_record(
                sample_id=sample_id, label=label, event_id=event_id,
                source_role=source_role, source_symbol=source_symbol,
                interval_minutes=interval, target_minutes=target_minutes, bars=bars,
            )
            self.conn.execute(
                'insert into quality values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    q['sample_id'], q['label'], q['event_id'], q['source_role'], q['source_symbol'],
                    q['interval_minutes'], q['target_minutes'], q['target_bar_count'],
                    q['actual_bar_count'], q['coverage_ratio'], q['first_open_time'],
                    q['last_close_time'], q['gap_count'], q['duplicate_count'],
                    q['non_monotonic_count'], int(bool(q['complete_enough'])),
                ),
            )
            linked_rows += len(bars)
            self.quality_count += 1
        self.bar_reference_count += linked_rows
        self.conn.commit()
        return linked_rows

    def finalise_files(self, metadata: dict[str, object]) -> dict[str, int]:
        self.conn.execute('pragma wal_checkpoint(TRUNCATE)')
        unique_bar_rows = int(self.conn.execute('select count(*) from bars').fetchone()[0])
        _write_query_csv(self.conn, self.folder / 'samples.csv', 'select * from samples order by anchor_time,sample_id', SAMPLE_FIELDS)
        _write_query_csv(self.conn, self.folder / 'outcomes.csv', 'select * from outcomes order by sample_id', OUTCOME_FIELDS)
        _write_query_csv(
            self.conn, self.folder / 'sample_windows.csv',
            'select * from sample_windows order by sample_id,source_role,source_symbol,interval_minutes',
            WINDOW_FIELDS,
        )
        _write_query_csv(
            self.conn, self.folder / 'quality.csv',
            'select * from quality order by sample_id,source_role,source_symbol,interval_minutes',
            QUALITY_FIELDS,
        )
        counts = {
            'event_group_count': len(self.event_ids),
            'sample_count': self.sample_count,
            'sample_bar_reference_count': self.bar_reference_count,
            'unique_bar_rows': unique_bar_rows,
            'quality_row_count': self.quality_count,
        }
        (self.folder / 'protocol.json').write_text(
            json.dumps({**metadata, 'split': self.split, 'part': self.part, 'counts': counts}, indent=2, default=str),
            encoding='utf-8',
        )
        (self.folder / 'README.txt').write_text(_split_readme(self.split), encoding='utf-8')
        (self.folder / 'CHATGPT_ANALYSIS_PROTOCOL.md').write_text(
            _chatgpt_protocol(self.split), encoding='utf-8'
        )
        self.conn.close()
        return counts


class RawEvidencePackageBuilder:
    """Write normalised raw evidence to sharded SQLite packages."""

    def __init__(
        self,
        settings: Settings,
        context_job_id: str,
        events: list[dict[str, object]],
        metadata: dict[str, object],
    ) -> None:
        self.settings = settings
        self.context_job_id = context_job_id
        self.metadata = metadata
        self.root = settings.temp_data_dir / 'exports' / context_job_id
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.split_map = event_splits(events)
        self.shards: dict[tuple[str, int], _Shard] = {}
        self.event_target: dict[str, tuple[str, int]] = {}
        for split, event_ids in self.split_map.items():
            for part, group in enumerate(_chunks(event_ids, EVENT_GROUPS_PER_SHARD), start=1):
                folder = self.root / f'{split}_part_{part:03d}'
                shard = _Shard(folder, split, part, group)
                self.shards[(split, part)] = shard
                for event_id in group:
                    self.event_target[event_id] = (split, part)

    def add_sample(
        self,
        *,
        sample: dict[str, object],
        outcome: dict[str, object],
        subject_15m: list[Kline],
        subject_1m: list[Kline],
        market_15m: dict[str, list[Kline]],
        market_1m: dict[str, list[Kline]],
    ) -> int:
        event_id = str(sample['event_id'])
        try:
            target = self.event_target[event_id]
        except KeyError as exc:
            raise RuntimeError(f'No evidence split assigned for event {event_id}') from exc
        return self.shards[target].add_sample(
            sample=sample,
            outcome=outcome,
            subject_15m=subject_15m,
            subject_1m=subject_1m,
            market_15m=market_15m,
            market_1m=market_1m,
        )

    def finalise(self) -> list[dict[str, object]]:
        outputs: list[dict[str, object]] = []
        split_manifests: dict[str, list[dict[str, object]]] = {key: [] for key in self.split_map}
        parts_by_split = {split: sum(1 for key in self.shards if key[0] == split) for split in self.split_map}
        for (split, part), shard in sorted(self.shards.items()):
            counts = shard.finalise_files(self.metadata)
            filename = _package_filename(split, part, parts_by_split[split])
            zip_path = self.root / filename
            digest = _zip_folder(shard.folder, zip_path)
            storage_path = f'raw-evidence/{self.context_job_id}/{filename}'
            role = split if parts_by_split[split] == 1 else f'{split}_part_{part:03d}'
            record = _register_upload(
                self.settings, self.context_job_id, zip_path, storage_path, digest, role
            )
            outputs.append(record)
            split_manifests[split].append({
                'part': part,
                'filename': filename,
                'size_bytes': zip_path.stat().st_size,
                'sha256': digest,
                **counts,
            })

        discovery_files = [part['filename'] for part in split_manifests['discovery']]
        validation_files = [part['filename'] for part in split_manifests['validation']]
        sealed_files = [part['filename'] for part in split_manifests['sealed_test']]
        index_folder = self.root / 'index'
        index_folder.mkdir(parents=True, exist_ok=True)
        index_payload = {
            **self.metadata,
            'shard_size_event_groups': EVENT_GROUPS_PER_SHARD,
            'splits': split_manifests,
            'files_inside_each_evidence_package': {
                'raw_evidence.sqlite': 'Normalised raw bars, samples, outcomes, sample windows, quality tables and a lookahead-safe sample_bars view.',
                'samples.csv': 'Small human-readable sample index.',
                'outcomes.csv': 'Labels and outcomes, separate from raw predictor bars.',
                'sample_windows.csv': 'Exact point-in-time windows ChatGPT must use for each sample.',
                'quality.csv': 'Coverage, gaps, duplicates and monotonicity checks.',
            },
            'instructions': {
                'first_review': ['binance10_index.zip', *discovery_files],
                'analysis_owner': 'ChatGPT derives features, finds patterns and interprets evidence.',
                'application_boundary': 'The app detects events, selects neutral controls and packages raw evidence only.',
                'validation_files': validation_files,
                'validation': 'Open only after discovery rules and acceptance criteria are frozen.',
                'sealed_test_files': sealed_files,
                'sealed_test': 'Do not open until validation passes without retuning.',
            },
        }
        (index_folder / 'manifest.json').write_text(
            json.dumps(index_payload, indent=2, default=str), encoding='utf-8'
        )
        (index_folder / 'README.txt').write_text(
            'Upload this index and every discovery part to ChatGPT first. The app has not engineered predictors or identified patterns.\n',
            encoding='utf-8',
        )
        (index_folder / 'CHATGPT_ANALYSIS_PROTOCOL.md').write_text(
            _chatgpt_protocol('index'), encoding='utf-8'
        )
        index_path = self.root / 'binance10_index.zip'
        index_digest = _zip_folder(index_folder, index_path)
        index_record = _register_upload(
            self.settings,
            self.context_job_id,
            index_path,
            f'raw-evidence/{self.context_job_id}/binance10_index.zip',
            index_digest,
            'index',
        )
        outputs.insert(0, index_record)
        return outputs


def _package_filename(split: str, part: int, part_count: int) -> str:
    if split == 'sealed_test':
        return 'SEALED_TEST_DO_NOT_OPEN.zip' if part_count == 1 else f'SEALED_TEST_DO_NOT_OPEN_part_{part:03d}.zip'
    return f'binance10_{split}.zip' if part_count == 1 else f'binance10_{split}_part_{part:03d}.zip'


def _split_readme(split: str) -> str:
    if split == 'sealed_test':
        return (
            'SEALED TEST. Do not inspect this package until discovery rules, thresholds, exclusions and validation acceptance criteria are frozen, and validation has passed without retuning.\n'
        )
    return (
        'This package contains normalised raw point-in-time evidence. The app has not engineered predictor features or selected a pattern. '
        'Use sample_windows to retrieve bars for each sample; every window ends strictly before its anchor. Labels and outcomes are isolated from bars.\n'
    )


def _chatgpt_protocol(split: str) -> str:
    opening = (
        '# ChatGPT blank-canvas analysis protocol\n\n'
        'The application has deliberately not identified patterns or calculated predictor features. '
        'ChatGPT owns feature generation, hypothesis creation, testing and interpretation.\n\n'
    )
    if split == 'sealed_test':
        return opening + (
            'Do not inspect any file in this package until validation passes the frozen acceptance criteria without any rule or threshold changes.\n'
        )
    return opening + (
        '## Reading the SQLite evidence\n\n'
        '`bars` stores each exchange bar once. `sample_windows` maps each sample to its subject and market-reference history. '
        'Use the prebuilt `sample_bars` view, which applies the correct window and strict pre-anchor cutoff automatically.\n\n'
        '## Evidence boundaries\n\n'
        '- Treat each event and its controls as one group when resampling or cross-validating.\n'
        '- Do not use crossing time, minutes to cross, exit notional or any outcome as a predictor.\n'
        '- Inspect quality before analysis and report exclusions transparently.\n'
        '- Never read validation or sealed-test packages during discovery.\n\n'
        '## Discovery work\n\n'
        '1. Inspect raw sequence shapes without assuming returns, volatility or volume are the correct representation.\n'
        '2. Derive a broad candidate library from price path, volume path, trade count, taker imbalance, compression/expansion, acceleration, persistence, market-relative behaviour, timing and interactions.\n'
        '3. Compare events with same-symbol controls and test whether findings recur across symbols and chronological subperiods.\n'
        '4. Use grouped resampling and multiple-testing controls; reject findings driven by a few coins or observations.\n'
        '5. Include realistic trigger timing, entry delay, fees, slippage sensitivity and no-trade frequency before calling a relationship actionable.\n'
        '6. Produce a small set of understandable candidate rules with exact frozen definitions, thresholds, exclusions and acceptance criteria.\n'
        '7. Do not open validation while changing discovery rules.\n\n'
        '## Required discovery output\n\n'
        '- Data-quality report and exclusions.\n'
        '- All candidate families tested, including failures.\n'
        '- Event-versus-control effect sizes and uncertainty.\n'
        '- Stability by symbol, liquidity, market regime and time period.\n'
        '- Frozen candidate-rule specification and validation acceptance criteria.\n'
    )
