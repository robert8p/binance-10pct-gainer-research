from __future__ import annotations

import base64
from pathlib import Path
import time
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests

from .config import Settings


class StorageError(RuntimeError):
    pass


TUS_CHUNK_SIZE = 6 * 1024 * 1024
DELETE_BATCH_SIZE = 100
LIST_PAGE_SIZE = 1000


def _admin_headers(secret_key: str, *, content_type: str | None = None) -> dict[str, str]:
    """Build server-side Supabase headers for opaque and legacy secret keys."""
    headers = {'apikey': secret_key, 'Authorization': f'Bearer {secret_key}'}
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


def _normalise_prefix(prefix: str) -> str:
    return prefix.strip('/')


def _join_storage_path(prefix: str, name: str) -> str:
    clean_prefix = _normalise_prefix(prefix)
    clean_name = name.strip('/')
    if not clean_prefix:
        return clean_name
    if clean_name == clean_prefix or clean_name.startswith(f'{clean_prefix}/'):
        return clean_name
    return f'{clean_prefix}/{clean_name}'


def _standard_upload(
    settings: Settings,
    local_path: Path,
    storage_path: str,
    content_type: str,
) -> None:
    url = f'{settings.supabase_url}/storage/v1/object/{settings.storage_bucket}/{quote(storage_path, safe="/")}'
    headers = _admin_headers(settings.supabase_secret_key, content_type=content_type)
    # Every export attempt uses a unique prefix. A collision is therefore a bug,
    # not something that should be silently overwritten.
    headers['x-upsert'] = 'false'
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
        'x-upsert': 'false',
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
                    if patch.status_code == 409:
                        # A 409 is either an offset race or a path/concurrency collision.
                        # Resume only when the server demonstrably advanced; otherwise fail
                        # immediately rather than sending the same conflicting PATCH five times.
                        server_offset = _resume_offset(upload_url, auth_headers)
                        if server_offset > offset:
                            offset = server_offset
                            if offset >= total:
                                break
                            handle.seek(offset)
                            chunk = handle.read(TUS_CHUNK_SIZE)
                            patch_headers['Upload-Offset'] = str(offset)
                            continue
                        raise StorageError(
                            f'Resumable upload conflict at offset {offset} (409): {patch.text[:500]}'
                        )
                    if patch.status_code in {423, 429, 500, 502, 503, 504}:
                        server_offset = _resume_offset(upload_url, auth_headers)
                        if server_offset >= total:
                            offset = server_offset
                            break
                        if server_offset != offset:
                            offset = server_offset
                            handle.seek(offset)
                            chunk = handle.read(TUS_CHUNK_SIZE)
                            patch_headers['Upload-Offset'] = str(offset)
                        continue
                    raise StorageError(
                        f'Resumable upload chunk failed ({patch.status_code}): {patch.text[:500]}'
                    )
                except StorageError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if attempt == 5:
                        raise StorageError(f'Resumable upload failed at offset {offset}: {last_error}') from exc
                    try:
                        server_offset = _resume_offset(upload_url, auth_headers)
                        if server_offset >= total:
                            offset = server_offset
                            break
                        offset = server_offset
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


def object_info(settings: Settings, storage_path: str) -> dict:
    """Return Supabase's persisted object metadata for a private object."""
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise StorageError('Supabase storage is not configured')
    url = (
        f'{settings.supabase_url}/storage/v1/object/info/'
        f'{settings.storage_bucket}/{quote(storage_path, safe="/")}'
    )
    response = requests.get(url, headers=_admin_headers(settings.supabase_secret_key), timeout=120)
    if response.status_code != 200:
        raise StorageError(f'Object verification failed ({response.status_code}): {response.text[:500]}')
    try:
        return response.json()
    except ValueError as exc:
        raise StorageError('Object verification returned invalid JSON') from exc


def verify_upload(settings: Settings, storage_path: str, expected_size: int) -> dict:
    """Confirm the final object exists and its persisted size matches the local ZIP."""
    last_error: Exception | None = None
    for delay in (0, 1, 2, 4, 8):
        if delay:
            time.sleep(delay)
        try:
            payload = object_info(settings, storage_path)
            metadata = payload.get('metadata') if isinstance(payload, dict) else None
            size_value = None
            if isinstance(metadata, dict):
                size_value = metadata.get('size')
            if size_value is None and isinstance(payload, dict):
                size_value = payload.get('size')
            if size_value is None:
                raise StorageError(f'Object verification returned no size for {storage_path}')
            try:
                actual_size = int(size_value)
            except (TypeError, ValueError) as exc:
                raise StorageError(
                    f'Object verification returned invalid size for {storage_path}: {size_value!r}'
                ) from exc
            if actual_size != expected_size:
                raise StorageError(
                    f'Object size mismatch for {storage_path}: Supabase has {actual_size}, expected {expected_size}'
                )
            return payload
        except StorageError as exc:
            last_error = exc
    raise StorageError(f'Upload verification did not converge for {storage_path}: {last_error}')


def _list_level(settings: Settings, prefix: str) -> list[dict]:
    """List one Supabase Storage folder level with pagination."""
    url = f'{settings.supabase_url}/storage/v1/object/list/{settings.storage_bucket}'
    headers = _admin_headers(settings.supabase_secret_key, content_type='application/json')
    items: list[dict] = []
    offset = 0
    while True:
        body = {
            'prefix': _normalise_prefix(prefix),
            'limit': LIST_PAGE_SIZE,
            'offset': offset,
            'sortBy': {'column': 'name', 'order': 'asc'},
        }
        response = requests.post(url, headers=headers, json=body, timeout=120)
        if response.status_code != 200:
            raise StorageError(f'Object listing failed ({response.status_code}): {response.text[:500]}')
        try:
            page = response.json()
        except ValueError as exc:
            raise StorageError('Object listing returned invalid JSON') from exc
        if not isinstance(page, list):
            raise StorageError(f'Object listing returned unexpected payload: {str(page)[:500]}')
        items.extend(page)
        if len(page) < LIST_PAGE_SIZE:
            break
        offset += len(page)
    return items


def list_objects(settings: Settings, prefix: str) -> list[str]:
    """Recursively list actual object paths under a bucket prefix."""
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise StorageError('Supabase storage is not configured')
    root = _normalise_prefix(prefix)
    queue = [root]
    visited: set[str] = set()
    objects: list[str] = []
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for item in _list_level(settings, current):
            name = str(item.get('name') or '').strip('/')
            if not name:
                continue
            full_path = _join_storage_path(current, name)
            # Supabase represents folders as list entries without an object id or
            # metadata. Actual objects have an id and/or metadata timestamps.
            is_object = bool(
                item.get('id')
                or item.get('metadata') is not None
                or item.get('created_at')
                or item.get('updated_at')
            )
            if is_object:
                objects.append(full_path)
            else:
                queue.append(full_path)
    return sorted(set(objects))


def _delete_paths(settings: Settings, paths: list[str]) -> None:
    if not paths:
        return
    url = f'{settings.supabase_url}/storage/v1/object/{settings.storage_bucket}'
    headers = _admin_headers(settings.supabase_secret_key, content_type='application/json')
    response = requests.delete(url, headers=headers, json={'prefixes': paths}, timeout=180)
    if response.status_code not in {200, 204}:
        raise StorageError(f'Object deletion failed ({response.status_code}): {response.text[:500]}')


def delete_prefix(settings: Settings, prefix: str) -> list[str]:
    """Delete and verify every object beneath a job-specific Storage prefix."""
    objects = list_objects(settings, prefix)
    for index in range(0, len(objects), DELETE_BATCH_SIZE):
        _delete_paths(settings, objects[index:index + DELETE_BATCH_SIZE])
    for delay in (0, 1, 2, 4):
        if delay:
            time.sleep(delay)
        remaining = list_objects(settings, prefix)
        if not remaining:
            return objects
    raise StorageError(
        f'Storage cleanup verification failed for {prefix}; {len(remaining)} object(s) remain: '
        f'{remaining[:10]}'
    )


def iter_download(settings: Settings, storage_path: str):
    """Stream a private object without loading a large evidence ZIP into RAM."""
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise StorageError('Supabase storage is not configured')
    url = (
        f'{settings.supabase_url}/storage/v1/object/authenticated/'
        f'{settings.storage_bucket}/{quote(storage_path, safe="/")}'
    )
    headers = _admin_headers(settings.supabase_secret_key)
    with requests.get(url, headers=headers, timeout=300, stream=True) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                yield chunk


def download(settings: Settings, storage_path: str) -> bytes:
    """Small-object compatibility helper used by tests and older callers."""
    return b''.join(iter_download(settings, storage_path))
