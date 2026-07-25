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
