"""WEB-05c2: language selection shares ui.json with the device menu."""
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from sonoscdplayer.ui.language_state import LanguageConflict, UiState
from sonoscdplayer.web.server import make_handler
from sonoscdplayer.web.state import WebPreferences
from sonoscdplayer.web.settings_read import settings_snapshot


def test_ui_and_web_writers_share_revision_and_lock(tmp_path):
    state = UiState(tmp_path, log=lambda *_: None)
    old = state.revision()
    assert old == 'none'
    assert state.save_language('en')
    assert state.language(('de', 'en')) == 'en'
    with pytest.raises(LanguageConflict):
        state.save_if_revision('de', old)
    revision = state.revision()
    assert state.save_if_revision('de', revision) != revision
    assert state.language(('de', 'en')) == 'de'
    assert json.loads((tmp_path / 'ui.json').read_text()) == {'version': 1, 'language': 'de'}
    with pytest.raises(ValueError):
        state.save_if_revision('fr', state.revision())
    with pytest.raises(ValueError):
        state.save_if_revision('de', 'invalid')


def test_language_settings_endpoint_is_isolated_and_conflict_safe(tmp_path, monkeypatch):
    config = tmp_path / 'etc'
    config.mkdir()
    (config / 'player.toml').write_text('[streaming]\nmax_buffer_mib = 16\n')
    (config / 'ui.toml').write_text('[ui]\nlanguage = "de"\n')
    monkeypatch.setenv('SONOSCDPLAYER_CONFIG_DIR', str(config))
    state = tmp_path / 'var'
    state.mkdir()

    class NoPlayerCalls:
        def request(self, *_args, **_kwargs):
            raise AssertionError('language updates must not contact player')

    http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(NoPlayerCalls(), WebPreferences(state)))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{http.server_port}'

    def get(path):
        with urlopen(base + path, timeout=3) as response:
            return json.load(response)

    def post(payload):
        req = Request(base + '/api/settings/language', data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        snapshot = get('/api/settings')
        revision = snapshot['display']['language_revision']
        assert revision == 'none'
        assert get('/api/i18n')['language'] == 'de'
        bad, _ = post({'language': 'fr', 'revision': revision})
        assert bad == 400
        assert not (state / 'ui.json').exists()
        status, saved = post({'language': 'en', 'revision': revision})
        assert status == 200 and len(saved['revision']) == 64
        assert get('/api/i18n')['language'] == 'en'
        assert get('/api/settings')['display']['language_revision'] == saved['revision']
        assert post({'language': 'de', 'revision': revision})[0] == 409
        assert UiState(state).save_language('de')
        assert post({'language': 'en', 'revision': saved['revision']})[0] == 409
        assert get('/api/i18n')['language'] == 'de'
        assert (config / 'ui.toml').read_text() == '[ui]\nlanguage = "de"\n'
        assert not (state / 'player-settings.json').exists()
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_language_symlink_is_never_overwritten(tmp_path):
    target = tmp_path / 'protected'
    target.write_text('original')
    state = UiState(tmp_path)
    state.path.symlink_to(target)
    with pytest.raises(OSError):
        state.revision()
    assert not state.save_language('en')
    assert target.read_text() == 'original'
