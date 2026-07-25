from __future__ import annotations

import base64
from pathlib import Path
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from .config import Settings


class StorageError(RuntimeError):
    pass


TUS_CHUNK_SIZE = 6 * 1024 * 1024


def _admin_headers(secret_key: str, *, content_type: str | None = None) -> dict[str, str]:
    """Build server-side Supabase headers for opaque and legacy secret keys."""
    headers = {'apikey': secret_key}
    # Supabase accepts the API key duplicated as a bearer credential and
    # translates opaque sb_secret_* values internally to service_role auth.
    headers['Authorization'] = f'Bearer {secret_key}'
    if content_type:
        headers['Content-Type'] = content_type
    return headers


def _direct_storage_base(supabase_url: str) -> str:
    parsed = urlparse(supabase_url)
    hostname = parsed.hostname or ''
    if hostname.endswith('.supabase.co') and '.storage.supabase.co' not in hostname:
        project_ref = hostname.removesuffix('.supabase.co')
        hostname = f'{project_ref}.storage.supabase.co'
        if parsed.port:
            hostname = f'{hostname}:{parsed.port}'
        return urlunparse((parsed.scheme or 'https', hostname, '', '', '', '')).rstrip('/')
    return supabase_url.rstrip('/')


def _metadata_value(value: str) -> str:
    return base64.b64encode(value.encode('utf-8')).decode('ascii')


def _standard_upload(
    settings: Settings,
    local_path: Path,
    storage_path: str,
    content_type: str,
) -> None:
    url = f'{settings.supabase_url}/storage/v1/object/{settings.storage_bucket}/{storage_path}'
    headers = _admin_headers(settings.supabase_secret_key, content_type=content_type)
    headers['x-upsert'] = 'true'
    with local_path.open('rb') as handle:
        response = requests.post(url, headers=headers, data=handle, timeout=300)
    if response.status_code not in {200, 201}:
        raise StorageError(f'Upload failed ({response.status_code}): {response.text[:500]}')


def _resume_offset(upload_url: str, headers: dict[str, str]) -> int:
    response = requests.head(
        upload_url,
        headers={**headers, 'Tus-Resumable': '1.0.0'},
        timeout=90,
    )
    response.raise_for_status()
    return int(response.headers.get('Upload-Offset', '0'))


def _tus_upload(
    settings: Settings,
    local_path: Path,
    storage_path: str,
    content_type: str,
) -> None:
    endpoint = f'{_direct_storage_base(settings.supabase_url)}/storage/v1/upload/resumable'
    auth_headers = _admin_headers(settings.supabase_secret_key)
    metadata = ','.join([
        f'bucketName {_metadata_value(settings.storage_bucket)}',
        f'objectName {_metadata_value(storage_path)}',
        f'contentType {_metadata_value(content_type)}',
        f'cacheControl {_metadata_value("3600")}',
    ])
    create_headers = {
        **auth_headers,
        'Tus-Resumable': '1.0.0',
        'Upload-Length': str(local_path.stat().st_size),
        'Upload-Metadata': metadata,
        'x-upsert': 'true',
    }
    response = requests.post(endpoint, headers=create_headers, data=b'', timeout=120)
    if response.status_code not in {201, 204}:
        raise StorageError(f'Resumable upload creation failed ({response.status_code}): {response.text[:500]}')
    location = response.headers.get('Location')
    if not location:
        raise StorageError('Resumable upload creation returned no Location header')
    upload_url = urljoin(endpoint, location)

    total = local_path.stat().st_size
    offset = 0
    with local_path.open('rb') as handle:
        while offset < total:
            handle.seek(offset)
            chunk = handle.read(TUS_CHUNK_SIZE)
            if not chunk:
                break
            patch_headers = {
                **auth_headers,
                'Tus-Resumable': '1.0.0',
                'Upload-Offset': str(offset),
                'Content-Type': 'application/offset+octet-stream',
            }
            last_error: Exception | None = None
            for attempt, delay in enumerate((0, 3, 5, 10, 20), start=1):
                if delay:
                    time.sleep(delay)
                try:
                    patch = requests.patch(upload_url, headers=patch_headers, data=chunk, timeout=300)
                    if patch.status_code == 204:
                        new_offset = int(patch.headers.get('Upload-Offset', str(offset + len(chunk))))
                        if new_offset <= offset:
                            raise StorageError(f'Resumable upload did not advance beyond offset {offset}')
                        offset = new_offset
                        break
                    if patch.status_code in {409, 423, 429, 500, 502, 503, 504}:
                        offset = _resume_offset(upload_url, auth_headers)
                        if offset >= total:
                            break
                        handle.seek(offset)
                        chunk = handle.read(TUS_CHUNK_SIZE)
                        patch_headers['Upload-Offset'] = str(offset)
                        continue
                    raise StorageError(
                        f'Resumable upload chunk failed ({patch.status_code}): {patch.text[:500]}'
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if attempt == 5:
                        raise StorageError(f'Resumable upload failed at offset {offset}: {last_error}') from exc
                    try:
                        offset = _resume_offset(upload_url, auth_headers)
                        if offset >= total:
                            break
                        handle.seek(offset)
                        chunk = handle.read(TUS_CHUNK_SIZE)
                        patch_headers['Upload-Offset'] = str(offset)
                    except Exception:
                        pass
            else:
                raise StorageError(f'Resumable upload failed at offset {offset}: {last_error}')
    if offset != total:
        raise StorageError(f'Resumable upload incomplete: uploaded {offset} of {total} bytes')


def upload(settings: Settings, local_path: Path, storage_path: str, content_type: str = 'application/zip') -> None:
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise StorageError('SUPABASE_URL and SUPABASE_SECRET_KEY are required')
    if local_path.stat().st_size > TUS_CHUNK_SIZE:
        _tus_upload(settings, local_path, storage_path, content_type)
    else:
        _standard_upload(settings, local_path, storage_path, content_type)


def iter_download(settings: Settings, storage_path: str):
    """Stream a private object without loading a large evidence ZIP into RAM."""
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise StorageError('Supabase storage is not configured')
    url = f'{settings.supabase_url}/storage/v1/object/authenticated/{settings.storage_bucket}/{storage_path}'
    headers = _admin_headers(settings.supabase_secret_key)
    with requests.get(url, headers=headers, timeout=300, stream=True) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                yield chunk


def download(settings: Settings, storage_path: str) -> bytes:
    """Small-object compatibility helper used by tests and older callers."""
    return b''.join(iter_download(settings, storage_path))
