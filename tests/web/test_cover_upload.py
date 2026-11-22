"""WEB-03c: cover endpoint passes only an opaque staging ID to the player."""
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json

import pytest
from sonoscdplayer.web.cover_upload import stage_upload, discard_upload
from sonoscdplayer.web.player_client import cover_payload
from test_web_client import running_server, post_json

TOKEN = '0' * 32


def test_stage_upload_bounds_and_cleanup(tmp_path):
    with pytest.raises(ValueError):
        stage_upload(BytesIO(b'a'), 4 * 1024 * 1024 + 1, tmp_path)
    with pytest.raises(ValueError):
        stage_upload(BytesIO(b'ab'), 4, tmp_path)
    assert list((tmp_path / 'cover-staging').glob('*.upload')) == []
    upload = stage_upload(BytesIO(b'abcdef'), 6, tmp_path)
    assert (tmp_path / 'cover-staging' / (upload + '.upload')).read_bytes() == b'abcdef'
    discard_upload(tmp_path, upload)
    assert not list((tmp_path / 'cover-staging').glob('*.upload'))


def test_cover_payload_strict():
    assert cover_payload({'token': TOKEN}, 'remove') == {'command': 'remove-cover', 'token': TOKEN}
    with pytest.raises(ValueError):
        cover_payload({'token': TOKEN, 'path': 'unsafe'}, 'remove')
    with pytest.raises(ValueError):
        cover_payload({'token': TOKEN, 'upload_id': '../unsafe'}, 'upload')


def test_upload_and_remove_are_forwarded_once(tmp_path):
    with running_server(tmp_path) as (base, requests):
        req = Request(base + '/api/cover/upload', data=b'some image', method='POST',
                      headers={'Content-Type': 'application/octet-stream', 'X-Disc-Token': TOKEN})
        with urlopen(req) as reply:
            assert reply.status == 202
            assert json.load(reply)['queued'] is True
        request = requests.pop()
        assert request['command'] == 'upload-cover'
        assert request['token'] == TOKEN
        assert len(request['upload_id']) == 32
        assert (tmp_path / 'state' / 'cover-staging' / (request['upload_id'] + '.upload')).is_file()
        discard_upload(tmp_path / 'state', request['upload_id'])
        assert post_json(base + '/api/cover/remove', {'token': TOKEN})['queued'] is True
        assert requests[-1] == {'command': 'remove-cover', 'token': TOKEN}


def test_upload_rejects_bad_identity_format_and_origin(tmp_path):
    with running_server(tmp_path) as (base, requests):
        for token, mime in [('invalid', 'application/octet-stream'), (TOKEN, 'text/plain')]:
            request = Request(base + '/api/cover/upload', data=b'abc', method='POST',
                              headers={'Content-Type': mime, 'X-Disc-Token': token})
            with pytest.raises(HTTPError) as caught:
                urlopen(request)
            assert caught.value.code in (400, 415)
        request = Request(base + '/api/cover/upload', data=b'abc', method='POST',
                          headers={'Content-Type':'application/octet-stream',
                                   'X-Disc-Token':TOKEN, 'Origin':'https://elsewhere.invalid'})
        with pytest.raises(HTTPError) as caught:
            urlopen(request)
        assert caught.value.code == 403
        assert requests == []
