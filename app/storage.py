from __future__ import annotations

from pathlib import Path
import requests

from .config import Settings


class StorageError(RuntimeError):
    pass


def upload(settings: Settings, local_path: Path, storage_path: str, content_type: str = 'application/zip') -> None:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise StorageError('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required')
    url = f'{settings.supabase_url}/storage/v1/object/{settings.storage_bucket}/{storage_path}'
    headers = {
        'apikey': settings.supabase_service_role_key,
        'Authorization': f'Bearer {settings.supabase_service_role_key}',
        'Content-Type': content_type,
        'x-upsert': 'true',
    }
    with local_path.open('rb') as handle:
        response = requests.post(url, headers=headers, data=handle, timeout=180)
    if response.status_code not in {200, 201}:
        raise StorageError(f'Upload failed ({response.status_code}): {response.text[:500]}')


def download(settings: Settings, storage_path: str) -> bytes:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise StorageError('Supabase storage is not configured')
    url = f'{settings.supabase_url}/storage/v1/object/authenticated/{settings.storage_bucket}/{storage_path}'
    headers = {
        'apikey': settings.supabase_service_role_key,
        'Authorization': f'Bearer {settings.supabase_service_role_key}',
    }
    response = requests.get(url, headers=headers, timeout=180)
    response.raise_for_status()
    return response.content
