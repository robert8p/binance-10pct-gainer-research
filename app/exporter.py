from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .config import Settings
from .db import connect
from .storage import upload

UTC = timezone.utc


def _write_csv(path: Path, rows: list[dict[str, object]], fallback_fields: list[str] | None = None) -> None:
    fields: list[str] = sorted({key for row in rows for key in row}) or (fallback_fields or [])
    with path.open('w', newline='', encoding='utf-8') as handle:
        if not fields:
            return
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def _zip_folder(folder: Path, output: Path) -> str:
    with ZipFile(output, 'w', ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(folder.iterdir()):
            archive.write(path, arcname=path.name)
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


def _event_splits(sample_rows: list[dict[str, object]]) -> dict[str, set[str]]:
    event_rows = [row for row in sample_rows if row.get('label') == 'event']
    ordered = sorted(event_rows, key=lambda row: str(row.get('anchor_time', '')))
    event_ids = [str(row['event_id']) for row in ordered]
    n = len(event_ids)
    if n < 5:
        return {'discovery': set(event_ids), 'validation': set(), 'sealed_test': set()}
    discovery_n = max(1, math.floor(n * 0.60))
    validation_n = max(1, math.floor(n * 0.20))
    if discovery_n + validation_n >= n:
        validation_n = 1
        discovery_n = n - 2
    return {
        'discovery': set(event_ids[:discovery_n]),
        'validation': set(event_ids[discovery_n:discovery_n + validation_n]),
        'sealed_test': set(event_ids[discovery_n + validation_n:]),
    }


def build_context_packages(
    settings: Settings,
    context_job_id: str,
    sample_rows: list[dict[str, object]],
    feature_rows: list[dict[str, object]],
    metadata: dict[str, object],
) -> list[dict[str, object]]:
    root = settings.temp_data_dir / 'exports' / context_job_id
    root.mkdir(parents=True, exist_ok=True)
    split_map = _event_splits(sample_rows)
    outputs: list[dict[str, object]] = []
    split_manifests: dict[str, dict[str, object]] = {}

    for split, event_ids in split_map.items():
        folder = root / split
        folder.mkdir(parents=True, exist_ok=True)
        split_samples = [row for row in sample_rows if str(row.get('event_id')) in event_ids]
        sample_ids = {str(row['sample_id']) for row in split_samples}
        split_features = [row for row in feature_rows if str(row.get('sample_id')) in sample_ids]
        split_protocol = {
            **metadata,
            'split': split,
            'event_group_count': len(event_ids),
            'sample_count': len(split_samples),
            'feature_row_count': len(split_features),
            'sealed': split == 'sealed_test',
        }
        _write_csv(folder / 'samples.csv', split_samples, ['sample_id','label','event_id','symbol','anchor_time'])
        _write_csv(folder / 'features.csv', split_features, ['sample_id','label','symbol','anchor_time','snapshot_offset_minutes'])
        (folder / 'protocol.json').write_text(json.dumps(split_protocol, indent=2, default=str), encoding='utf-8')
        warning = (
            'SEALED TEST. Do not inspect until discovery rules and validation acceptance criteria are frozen.\n'
            if split == 'sealed_test'
            else 'Predictors end before each decision timestamp. Outcome columns remain in samples.csv.\n'
        )
        (folder / 'README.txt').write_text(warning, encoding='utf-8')
        filename = (
            'SEALED_TEST_DO_NOT_OPEN.zip' if split == 'sealed_test'
            else f'binance10_{split}.zip'
        )
        zip_path = root / filename
        digest = _zip_folder(folder, zip_path)
        storage_path = f'context/{context_job_id}/{filename}'
        record = _register_upload(settings, context_job_id, zip_path, storage_path, digest, split)
        outputs.append(record)
        split_manifests[split] = {
            'event_group_count': len(event_ids),
            'sample_count': len(split_samples),
            'feature_row_count': len(split_features),
            'filename': filename,
            'sha256': digest,
        }

    index_folder = root / 'index'
    index_folder.mkdir(parents=True, exist_ok=True)
    index_payload = {
        **metadata,
        'splits': split_manifests,
        'instructions': {
            'first_review': ['binance10_index.zip', 'binance10_discovery.zip'],
            'validation': 'Open only after discovery rules and thresholds are frozen.',
            'sealed_test': 'Do not open until validation acceptance criteria pass without retuning.',
        },
    }
    (index_folder / 'manifest.json').write_text(json.dumps(index_payload, indent=2, default=str), encoding='utf-8')
    (index_folder / 'README.txt').write_text(
        'Start with the index and discovery package. Keep validation and sealed test unopened until the research protocol permits them.\n',
        encoding='utf-8',
    )
    index_path = root / 'binance10_index.zip'
    index_digest = _zip_folder(index_folder, index_path)
    index_storage = f'context/{context_job_id}/binance10_index.zip'
    index_record = _register_upload(settings, context_job_id, index_path, index_storage, index_digest, 'index')
    outputs.insert(0, index_record)
    return outputs
