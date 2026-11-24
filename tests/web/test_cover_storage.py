"""WEB-07a: read-only statistics, no player RPC or names in replies."""
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from sonoscdplayer.web import cover_storage
from sonoscdplayer.web.cover_storage import cover_storage_snapshot
from sonoscdplayer.web.server import make_handler
from sonoscdplayer.web.state import WebPreferences


def test_bounded_storage_counts_only_regular_jpegs(tmp_path):
    (tmp_path / 'a.jpg').write_bytes(b'x' * 100)
    (tmp_path / 'manual-b.jpg').write_bytes(b'y' * 20)
    (tmp_path / 'other.png').write_bytes(b'z' * 12)
    (tmp_path / 'linked.jpg').symlink_to(tmp_path / 'other.png')
    info = cover_storage_snapshot(1, tmp_path)
    assert info == {'ok': True, 'cover_count': 2, 'manual_count': 1,
                    'automatic_count': 1, 'used_bytes': 120,
                    'manual_bytes': 20, 'automatic_bytes': 100,
                    'budget_bytes': 1024 * 1024}
    assert 'a.jpg' not in json.dumps(info)
    assert cover_storage_snapshot(1, tmp_path / 'absent')['cover_count'] == 0
    with pytest.raises(ValueError):
        cover_storage_snapshot(0, tmp_path)


def test_http_storage_does_not_contact_player(tmp_path, monkeypatch):
    config = tmp_path / 'etc'
    state = tmp_path / 'state'
    covers = tmp_path / 'covers'
    config.mkdir()
    state.mkdir()
    covers.mkdir()
    (config / 'ui.toml').write_text('[ui.cover]\nbudget_mb = 20\n'.replace('\\n', '\n'))
    (covers / 'manual-a.jpg').write_bytes(b'x' * 17)
    monkeypatch.setenv('SONOSCDPLAYER_CONFIG_DIR', str(config))
    monkeypatch.setattr(cover_storage, 'DEFAULT_COVER_DIR', covers)

    class NoPlayer:
        def request(self, *_args, **_kwargs):
            raise AssertionError('cover statistics must not contact player')

    http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(NoPlayer(), WebPreferences(state)))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{http.server_port}/api/covers/storage', timeout=3) as reply:
            data = json.load(reply)
        assert data['cover_count'] == data['manual_count'] == 1
        assert data['used_bytes'] == 17
        assert data['budget_bytes'] == 20 * 1024 * 1024
        assert 'manual-a.jpg' not in json.dumps(data)
        (config / 'ui.toml').write_text('[ui.cover]\nbudget_mb = -1\n'.replace('\\n', '\n'))
        with pytest.raises(HTTPError) as exc:
            urlopen(f'http://127.0.0.1:{http.server_port}/api/covers/storage', timeout=3)
        assert exc.value.code == 503
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)
