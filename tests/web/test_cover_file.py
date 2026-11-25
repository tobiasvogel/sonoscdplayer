"""AUD-01: current-cover serving cannot escape cache or race a path check."""
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from sonoscdplayer.web import cover_file
from sonoscdplayer.web.cover_file import MAX_SERVED_COVER_BYTES, read_player_cover
from sonoscdplayer.web.server import make_handler
from sonoscdplayer.web.state import WebPreferences


def test_regular_cover_read_and_bounds(tmp_path):
    cover = tmp_path / 'album.jpg'
    cover.write_bytes(b'jpeg-data')
    assert read_player_cover(str(cover), tmp_path) == (b'jpeg-data', 'image/jpeg')
    cover.write_bytes(b'x' * (MAX_SERVED_COVER_BYTES + 1))
    with pytest.raises(ValueError, match='invalid cover'):
        read_player_cover(str(cover), tmp_path)
    cover.write_bytes(b'')
    with pytest.raises(ValueError, match='invalid cover'):
        read_player_cover(str(cover), tmp_path)


def test_rejects_symlink_traversal_and_nonregular_file(tmp_path):
    outside = tmp_path.parent / 'outside.jpg'
    outside.write_bytes(b'private')
    cover = tmp_path / 'album.jpg'
    cover.symlink_to(outside)
    with pytest.raises(OSError):
        read_player_cover(str(cover), tmp_path)
    with pytest.raises(ValueError):
        read_player_cover(str(outside), tmp_path)
    with pytest.raises(ValueError):
        read_player_cover(str(tmp_path / '..' / 'outside.jpg'), tmp_path)
    (tmp_path / 'folder.jpg').mkdir()
    with pytest.raises(ValueError):
        read_player_cover(str(tmp_path / 'folder.jpg'), tmp_path)
    linked_root = tmp_path.parent / 'linked-cover-root'
    if not linked_root.exists() and not linked_root.is_symlink():
        linked_root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        read_player_cover(str(linked_root / 'album.jpg'), linked_root)


def test_swap_before_open_is_refused(tmp_path, monkeypatch):
    cover = tmp_path / 'safe.jpg'
    cover.write_bytes(b'original')
    private = tmp_path / 'private.txt'
    private.write_bytes(b'private')
    real_open = cover_file.os.open
    swapped = False

    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == 'safe.jpg' and not swapped:
            swapped = True
            cover.unlink()
            cover.symlink_to(private)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(cover_file.os, 'open', swap)
    with pytest.raises(OSError):
        read_player_cover(str(cover), tmp_path)
    assert private.read_bytes() == b'private'


def test_http_cover_uses_player_path_but_no_client_file_path(tmp_path, monkeypatch):
    cover = tmp_path / 'sample.jpg'
    cover.write_bytes(b'fixture')
    monkeypatch.setattr(cover_file, 'DEFAULT_COVER_DIR', tmp_path)

    class Player:
        def request(self, payload, **_kwargs):
            assert payload == {'command': 'status'}
            return {'ok': True, 'state': {'metadata': {'cover_file': str(cover)}}}

    http = ThreadingHTTPServer(('127.0.0.1', 0),
                               make_handler(Player(), WebPreferences(tmp_path / 'state')))
    worker = Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        endpoint = f'http://127.0.0.1:{http.server_port}/api/cover'
        with urlopen(endpoint, timeout=3) as reply:
            assert reply.read() == b'fixture'
            assert reply.headers.get_content_type() == 'image/jpeg'
        cover.unlink()
        cover.symlink_to(tmp_path / 'state')
        with pytest.raises(HTTPError) as caught:
            urlopen(endpoint, timeout=3)
        assert caught.value.code == 404
    finally:
        http.shutdown()
        http.server_close()
        worker.join(timeout=3)
