"""WEB-07a: read-only statistics, no player RPC or names in replies."""
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from sonoscdplayer.web import cover_storage
from sonoscdplayer.web.cover_storage import cover_storage_snapshot, cover_kind, cover_inventory
from sonoscdplayer.web.server import make_handler
from sonoscdplayer.web.state import WebPreferences


def test_bounded_storage_counts_only_regular_jpegs(tmp_path):
    (tmp_path / ('a' * 64 + '.jpg')).write_bytes(b'x' * 100)
    (tmp_path / ('manual-' + 'b' * 32 + '.jpg')).write_bytes(b'y' * 20)
    (tmp_path / 'other.png').write_bytes(b'z' * 12)
    (tmp_path / ('c' * 64 + '.jpg')).symlink_to(tmp_path / 'other.png')
    (tmp_path / 'unmanaged.jpg').write_bytes(b'w' * 500)
    (tmp_path / ('manual-' + 'z' * 32 + '.jpg')).write_bytes(b'w' * 500)
    info = cover_storage_snapshot(1, tmp_path)
    assert info == {'ok': True, 'cover_count': 2, 'manual_count': 1,
                    'automatic_count': 1, 'used_bytes': 120,
                    'manual_bytes': 20, 'automatic_bytes': 100,
                    'budget_bytes': 1024 * 1024}
    assert 'a' * 64 not in json.dumps(info)
    assert cover_storage_snapshot(1, tmp_path / 'absent')['cover_count'] == 0
    with pytest.raises(ValueError):
        cover_storage_snapshot(0, tmp_path)



def test_private_inventory_has_stable_order_and_never_follows_symlinks(tmp_path):
    manual = 'manual-' + 'b' * 32 + '.jpg'
    automatic = 'a' * 64 + '.jpg'
    (tmp_path / manual).write_bytes(b'm' * 19)
    (tmp_path / automatic).write_bytes(b'a' * 23)
    (tmp_path / ('c' * 64 + '.jpg')).symlink_to(tmp_path / automatic)
    (tmp_path / 'unmanaged.jpg').write_bytes(b'x')
    found = cover_inventory(tmp_path)
    assert [(item.name, item.kind, item.size) for item in found] == [
        (automatic, 'automatic', 23), (manual, 'manual', 19)]
    assert all(item.inode > 0 and item.device >= 0 for item in found)
    assert cover_inventory(tmp_path / 'missing') == ()
    assert 'a' * 64 not in json.dumps(cover_storage_snapshot(1, tmp_path))


def test_private_inventory_rejects_non_directory(tmp_path):
    path = tmp_path / 'not-a-directory'
    path.write_text('x')
    with pytest.raises(NotADirectoryError):
        cover_inventory(path)


def test_only_known_store_filenames_are_classified():
    assert cover_kind('a' * 64 + '.jpg') == 'automatic'
    assert cover_kind('manual-' + 'a' * 32 + '.jpg') == 'manual'
    for name in ('a.jpg', 'manual-a.jpg', 'manual-' + 'g' * 32 + '.jpg',
                 'A' * 64 + '.jpg', 'a' * 64 + '.jpg.bak', '../' + 'a' * 64 + '.jpg',
                 'manual-' + 'a' * 32 + '.png'):
        assert cover_kind(name) is None


def test_http_storage_does_not_contact_player(tmp_path, monkeypatch):
    config = tmp_path / 'etc'
    state = tmp_path / 'state'
    covers = tmp_path / 'covers'
    config.mkdir()
    state.mkdir()
    covers.mkdir()
    (config / 'ui.toml').write_text('[ui.cover]\nbudget_mb = 20\n'.replace('\\n', '\n'))
    (covers / ('manual-' + 'a' * 32 + '.jpg')).write_bytes(b'x' * 17)
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
        assert 'manual-' + 'a' * 32 not in json.dumps(data)
        (config / 'ui.toml').write_text('[ui.cover]\nbudget_mb = -1\n'.replace('\\n', '\n'))
        with pytest.raises(HTTPError) as exc:
            urlopen(f'http://127.0.0.1:{http.server_port}/api/covers/storage', timeout=3)
        assert exc.value.code == 503
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)
