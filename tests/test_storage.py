from app.config import Settings
from app import storage


class Response:
    status_code = 200
    text = ''
    content = b'data'
    headers = {}

    def raise_for_status(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def iter_content(self, chunk_size):
        yield self.content


def test_new_secret_key_is_sent_as_api_key_and_bearer(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers, data, timeout):
        captured.update(headers)
        return Response()

    monkeypatch.setattr(storage.requests, 'post', fake_post)
    source = tmp_path / 'x.zip'
    source.write_bytes(b'x')
    settings = Settings(
        supabase_url='https://example.supabase.co',
        supabase_secret_key='sb_secret_example',
        temp_data_dir=tmp_path,
    )
    storage.upload(settings, source, 'x.zip')
    assert captured['apikey'] == 'sb_secret_example'
    assert captured['Authorization'] == 'Bearer sb_secret_example'


def test_legacy_service_role_key_retains_bearer(monkeypatch, tmp_path):
    captured = {}

    def fake_get(url, headers, timeout, stream):
        captured.update(headers)
        return Response()

    monkeypatch.setattr(storage.requests, 'get', fake_get)
    settings = Settings(
        supabase_url='https://example.supabase.co',
        supabase_secret_key='eyJlegacy',
        temp_data_dir=tmp_path,
    )
    assert storage.download(settings, 'x.zip') == b'data'
    assert captured['apikey'] == 'eyJlegacy'
    assert captured['Authorization'] == 'Bearer eyJlegacy'


def test_large_files_use_direct_resumable_upload(monkeypatch, tmp_path):
    calls = []

    class CreateResponse(Response):
        status_code = 201
        headers = {'Location': '/upload/abc'}

    class PatchResponse(Response):
        status_code = 204
        headers = {'Upload-Offset': str(storage.TUS_CHUNK_SIZE + 1)}

    def fake_post(url, headers, data, timeout):
        calls.append(('post', url, headers))
        return CreateResponse()

    def fake_patch(url, headers, data, timeout):
        calls.append(('patch', url, headers, len(data)))
        return PatchResponse()

    monkeypatch.setattr(storage.requests, 'post', fake_post)
    monkeypatch.setattr(storage.requests, 'patch', fake_patch)
    source = tmp_path / 'large.zip'
    source.write_bytes(b'x' * (storage.TUS_CHUNK_SIZE + 1))
    settings = Settings(
        supabase_url='https://projectref.supabase.co',
        supabase_secret_key='sb_secret_example',
        temp_data_dir=tmp_path,
    )
    storage.upload(settings, source, 'raw-evidence/large.zip')
    assert calls[0][1].startswith('https://projectref.storage.supabase.co/')
    assert calls[1][3] == storage.TUS_CHUNK_SIZE


def test_recursive_object_listing_handles_attempt_folders(monkeypatch, tmp_path):
    settings = Settings(
        supabase_url='https://example.supabase.co',
        supabase_secret_key='sb_secret_example',
        temp_data_dir=tmp_path,
    )

    def fake_list_level(_settings, prefix):
        if prefix == 'raw-evidence/job-1':
            return [{'name': 'attempt_abc', 'id': None, 'metadata': None}]
        if prefix == 'raw-evidence/job-1/attempt_abc':
            return [
                {'name': 'part_001.zip', 'id': 'object-1', 'metadata': {'size': 10}},
                {'name': 'part_002.zip', 'id': 'object-2', 'metadata': {'size': 20}},
            ]
        return []

    monkeypatch.setattr(storage, '_list_level', fake_list_level)
    assert storage.list_objects(settings, 'raw-evidence/job-1/') == [
        'raw-evidence/job-1/attempt_abc/part_001.zip',
        'raw-evidence/job-1/attempt_abc/part_002.zip',
    ]


def test_delete_prefix_removes_and_verifies_objects(monkeypatch, tmp_path):
    settings = Settings(
        supabase_url='https://example.supabase.co',
        supabase_secret_key='sb_secret_example',
        temp_data_dir=tmp_path,
    )
    listings = iter([
        ['raw-evidence/job-1/old.zip', 'raw-evidence/job-1/attempt_x/new.zip'],
        [],
    ])
    deleted = []

    monkeypatch.setattr(storage, 'list_objects', lambda *_args, **_kwargs: next(listings))

    def fake_delete(url, headers, json, timeout):
        deleted.extend(json['prefixes'])
        response = Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(storage.requests, 'delete', fake_delete)
    removed = storage.delete_prefix(settings, 'raw-evidence/job-1/')
    assert removed == deleted
    assert removed == [
        'raw-evidence/job-1/old.zip',
        'raw-evidence/job-1/attempt_x/new.zip',
    ]


def test_verify_upload_confirms_persisted_size(monkeypatch, tmp_path):
    settings = Settings(
        supabase_url='https://example.supabase.co',
        supabase_secret_key='sb_secret_example',
        temp_data_dir=tmp_path,
    )

    class InfoResponse(Response):
        status_code = 200

        def json(self):
            return {'metadata': {'size': '123'}}

    monkeypatch.setattr(storage.requests, 'get', lambda *args, **kwargs: InfoResponse())
    payload = storage.verify_upload(settings, 'raw-evidence/job/attempt/file.zip', 123)
    assert payload['metadata']['size'] == '123'


def test_tus_409_without_server_progress_fails_immediately(monkeypatch, tmp_path):
    import pytest

    settings = Settings(
        supabase_url='https://projectref.supabase.co',
        supabase_secret_key='sb_secret_example',
        temp_data_dir=tmp_path,
    )
    source = tmp_path / 'large.zip'
    source.write_bytes(b'x' * (storage.TUS_CHUNK_SIZE + 1))
    patch_calls = []

    class CreateResponse(Response):
        status_code = 201
        headers = {'Location': '/upload/abc'}

    class ConflictResponse(Response):
        status_code = 409
        text = 'duplicate key conflict'

    class HeadResponse(Response):
        status_code = 200
        headers = {'Upload-Offset': '0'}

    monkeypatch.setattr(storage.requests, 'post', lambda *args, **kwargs: CreateResponse())

    def fake_patch(*args, **kwargs):
        patch_calls.append(1)
        return ConflictResponse()

    monkeypatch.setattr(storage.requests, 'patch', fake_patch)
    monkeypatch.setattr(storage.requests, 'head', lambda *args, **kwargs: HeadResponse())

    with pytest.raises(storage.StorageError, match='conflict at offset 0'):
        storage.upload(settings, source, 'raw-evidence/job/attempt/file.zip')
    assert len(patch_calls) == 1
