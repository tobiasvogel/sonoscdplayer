"""WEB-05c3a: cover mode is staged independently of ui.toml and the player."""
import json
from http.server import ThreadingHTTPServer
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from sonoscdplayer.metadata.cover_preferences import read_cover_preferences
from sonoscdplayer.ui.cover_state import (
    CoverConflict, FILE_NAME, read_cover_mode, save_cover_mode,
)
from sonoscdplayer.web.server import make_handler
from sonoscdplayer.web.state import WebPreferences
from sonoscdplayer.web.settings_read import settings_snapshot


def test_staged_cover_mode_and_reset(tmp_path):
    base = tmp_path / 'ui.toml'
    base.write_text('[ui.cover]\nmode = "on"\nmax_dimension_px = 640\n')
    original = base.read_bytes()
    state = tmp_path / 'state'
    assert read_cover_mode(state) == (None, 'none')
    rev = save_cover_mode(state, 'off', 'none')
    assert len(rev) == 64
    assert (state / FILE_NAME).stat().st_mode & 0o777 == 0o600
    assert read_cover_preferences(base, state_dir=state).mode == 'off'
    assert read_cover_preferences(base).mode == 'on'
    assert read_cover_preferences(base, state_dir=state).max_dimension_px == 640
    assert base.read_bytes() == original
    with pytest.raises(CoverConflict):
        save_cover_mode(state, 'auto', 'none')
    assert save_cover_mode(state, None, rev) == 'none'
    assert read_cover_preferences(base, state_dir=state).mode == 'on'
    assert base.read_bytes() == original


@pytest.mark.parametrize('value', ['wrong', '', 1, True, {}, []])
def test_invalid_cover_value_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        save_cover_mode(tmp_path, value, 'none')
    assert not (tmp_path / FILE_NAME).exists()


def test_corrupt_and_symlink_file_cannot_be_overwritten(tmp_path):
    target = tmp_path / 'target'
    target.write_text('protected')
    filename = tmp_path / FILE_NAME
    filename.symlink_to(target)
    assert read_cover_mode(tmp_path) == (None, 'invalid')
    with pytest.raises(ValueError):
        save_cover_mode(tmp_path, 'on', 'invalid')
    assert target.read_text() == 'protected'
    filename.unlink()
    filename.write_bytes(b'{broken')
    assert read_cover_mode(tmp_path) == (None, 'invalid')
    with pytest.raises(ValueError):
        save_cover_mode(tmp_path, 'on', 'invalid')


def test_http_cover_staging_does_not_contact_player(tmp_path, monkeypatch):
    config = tmp_path / 'etc'
    state = tmp_path / 'state'
    config.mkdir()
    state.mkdir()
    (config / 'player.toml').write_text('[streaming]\nmax_buffer_mib = 16\n')
    (config / 'ui.toml').write_text('[ui.cover]\nmode = "on"\n')
    monkeypatch.setenv('SONOSCDPLAYER_CONFIG_DIR', str(config))

    class NoPlayerCalls:
        def request(self, *_args, **_kwargs):
            raise AssertionError('cover staging must not call the player')

    http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(NoPlayerCalls(), WebPreferences(state)))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{http.server_port}'

    def post(payload):
        request = Request(base + '/api/settings/cover', data=json.dumps(payload).encode('utf-8'),
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        snapshot = settings_snapshot(config_root=config, state_root=state)
        assert snapshot['display']['cover_revision'] == 'none'
        assert post({'mode': 'invalid', 'revision': 'none'})[0] == 400
        status, response = post({'mode': 'off', 'revision': 'none'})
        assert status == 200 and len(response['revision']) == 64
        assert post({'mode': 'auto', 'revision': 'none'})[0] == 409
        snapshot = settings_snapshot(config_root=config, state_root=state)
        row = next(row for row in snapshot['settings'] if row['key'] == 'ui.cover.mode')
        assert row['value'] == 'off' and row['base'] == 'on'
        assert row['origin_file'] == FILE_NAME
        assert post({'mode': None, 'revision': response['revision']})[0] == 200
        assert not (state / FILE_NAME).exists()
        # Corruption is recoverable only via explicit reset with its revision.
        (state / FILE_NAME).write_bytes(b'{broken')
        assert post({'mode': 'on', 'revision': 'invalid'})[0] == 400
        assert post({'mode': None, 'revision': 'none'})[0] == 409
        assert post({'mode': None, 'revision': 'invalid'})[0] == 200
        assert not (state / FILE_NAME).exists()
        assert (config / 'ui.toml').read_text() == '[ui.cover]\nmode = "on"\n'
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()
