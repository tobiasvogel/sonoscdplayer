"""I18N-02: player-originated errors are localized only at the web boundary."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.request import urlopen

import pytest

from sonoscdplayer.ui.i18n.translator import catalog
from sonoscdplayer.web.i18n import metadata_error_message, player_rejection_message
from sonoscdplayer.web.server import make_handler
from sonoscdplayer.web.state import WebPreferences


class _Player:
    def __init__(self, *, unknown=False):
        self.unknown = unknown
        self.calls = []

    def request(self, request, *, timeout=35):
        self.calls.append(request)
        if request == {'command': 'status'}:
            return {'ok': True, 'state': {
                'player': {'mode': 'ERROR', 'error': 'private player details'},
                'metadata': {'cover_error': ('private cover path' if self.unknown else
                                             'metadata.error.cover_budget_full')},
                'last_rejection': {'seq': 7, 'reason': (
                    'RuntimeError: secret RINCON_TEST at 192.0.2.15' if self.unknown
                    else 'ValueError: no confirmed target')},
            }}
        if request == {'command': 'dashboard'}:
            return {'ok': True, 'dashboard': {
                'track_count': 0,
                'edit_error': 'metadata.error.edit_save_failed',
                'release_error': ('private /path/and UID' if self.unknown else
                                  'metadata.error.musicbrainz_unavailable'),
            }}
        if request == {'command': 'library'}:
            return {'ok': True, 'library': {
                'status': 'error', 'seq': 2,
                'error': 'metadata.error.library_unavailable',
                'manage': {'status': 'error', 'seq': 2, 'error':
                           'metadata.error.library_edit_failed'},
                'maintenance': {'status': 'error', 'seq': 2, 'error':
                                'metadata.error.library_maintenance_failed'},
            }}
        raise AssertionError('Unexpected player command')


@contextmanager
def _http(tmp_path, *, language, unknown=False):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'ui.json').write_text(
        json.dumps({'version': 1, 'language': language}), encoding='utf-8')
    player = _Player(unknown=unknown)
    server = ThreadingHTTPServer(('127.0.0.1', 0),
                                 make_handler(player, WebPreferences(state)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', player
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _get(base, path):
    with urlopen(base + path, timeout=3) as result:
        return json.load(result)


@pytest.mark.parametrize('language', ['de', 'en'])
def test_player_errors_are_translated_in_http_responses(tmp_path, language):
    with _http(tmp_path, language=language) as (base, player):
        status = _get(base, '/api/status')
        dashboard = _get(base, '/api/dashboard')
        library = _get(base, '/api/library')['library']
    translations = catalog(language)
    assert status['metadata']['cover_error'] == translations['metadata.error.cover_budget_full']
    assert status['player']['error'] == translations['web.api.player_error']
    assert status['last_rejection']['reason'] == translations['web.error.playback.no_target']
    assert dashboard['edit_error'] == translations['metadata.error.edit_save_failed']
    assert dashboard['release_error'] == translations['metadata.error.musicbrainz_unavailable']
    assert library['error'] == translations['metadata.error.library_unavailable']
    assert library['manage']['error'] == translations['metadata.error.library_edit_failed']
    assert library['maintenance']['error'] == translations['metadata.error.library_maintenance_failed']
    assert player.calls == [{'command': 'status'}, {'command': 'dashboard'}, {'command': 'library'}]


def test_untrusted_legacy_errors_are_never_forwarded_to_lan(tmp_path):
    with _http(tmp_path, language='en', unknown=True) as (base, _):
        status = _get(base, '/api/status')
        dashboard = _get(base, '/api/dashboard')
    assert status['last_rejection']['reason'] == catalog('en')['web.error.playback.generic']
    assert dashboard['release_error'] == catalog('en')['metadata.error.generic']
    assert status['metadata']['cover_error'] == catalog('en')['metadata.error.generic']
    for forbidden in ('RINCON_TEST', '192.0.2.15', 'private', '/path/and UID'):
        assert forbidden not in json.dumps((status, dashboard))


def test_player_message_helpers_return_nothing_for_absent_errors(tmp_path):
    (tmp_path / 'ui.json').write_text('''{"version":1,"language":"de"}''', encoding='utf-8')
    assert metadata_error_message(tmp_path, None) is None
    assert player_rejection_message(tmp_path, None) is None
    assert player_rejection_message(tmp_path, '') is None
    assert metadata_error_message(tmp_path, 'Exception: secret') == catalog('de')[
        'metadata.error.generic']
    assert player_rejection_message(tmp_path, 'drive wake unverified; PLAY blocked') == catalog('de')[
        'web.error.playback.drive_wake']


def test_worker_error_codes_exist_in_every_catalog():
    source_root = Path(__file__).resolve().parents[2] / 'src' / 'sonoscdplayer' / 'metadata'
    sources = ('worker.py', 'manual_cover.py', 'release_workflow.py',
               'library_index.py', 'library_manage.py', 'library_maintenance.py')
    codes = set()
    for source in sources:
        codes.update(re.findall(r"['\"](metadata\.error\.[a-z_]+)['\"]",
                                (source_root / source).read_text(encoding='utf-8')))
    assert len(codes) == 9
    for language in ('de', 'en'):
        assert codes <= catalog(language).keys()
