"""WEB-05b: stage playback settings safely; apply only on next player start."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sonoscdplayer.player.playback_settings import (
    FILE_NAME, SettingsConflict, base_values, merged, read_overrides, update,
)
from sonoscdplayer.web.settings_read import settings_snapshot


BASE = {
    'streaming': {'max_buffer_mib': 16},
    'sonos': {'use_queue': True},
    'playback': {'continue': True},
}


def configured(tmp_path):
    config = tmp_path / 'etc'
    state = tmp_path / 'var'
    config.mkdir()
    state.mkdir()
    (config / 'player.toml').write_text('''[streaming]
max_buffer_mib = 16
[sonos]
use_queue = true
[playback]
continue = true
''', encoding='utf-8')
    return config, state


def command(state, *, values=None, action='save', revision=None):
    defaults = {'sonos.use_queue': False, 'playback.continue': False,
                'streaming.max_buffer_mib': 64}
    return {'action': action, 'revision': read_overrides(state)[1] if revision is None else revision,
            'values': defaults if values is None and action == 'save' else (values if values is not None else {})}


def test_stages_only_delta_without_modifying_base_toml(tmp_path):
    config, state = configured(tmp_path)
    path = config / 'player.toml'
    before = path.read_bytes()
    assert read_overrides(state) == ({}, 'none')
    revision = update(BASE, state, command(state))
    assert len(revision) == 64
    saved = state / FILE_NAME
    assert saved.stat().st_mode & 0o777 == 0o600
    assert path.read_bytes() == before
    parsed = json.loads(saved.read_text())
    assert parsed == {'version': 1, 'values': {'sonos.use_queue': False,
                                              'playback.continue': False,
                                              'streaming.max_buffer_mib': 64}}
    new_settings, again = merged(BASE, state_dir=state)
    assert again == revision
    assert new_settings['sonos']['use_queue'] is False
    assert new_settings['playback']['continue'] is False
    assert new_settings['streaming']['max_buffer_mib'] == 64
    assert BASE['streaming']['max_buffer_mib'] == 16
    assert BASE['playback']['continue'] is True
    snapshot = settings_snapshot(config_root=config, state_root=state)
    values = {item['key']: item for item in snapshot['settings']}
    assert values['streaming.max_buffer_mib']['value'] == 64
    assert values['streaming.max_buffer_mib']['base'] == 16
    assert values['streaming.max_buffer_mib']['source'] == 'state'
    assert values['streaming.max_buffer_mib']['origin_file'] == FILE_NAME
    assert snapshot['playback']['revision'] == revision
    assert snapshot['playback']['runtime'] is None


def test_reset_restores_toml_without_touching_file(tmp_path):
    config, state = configured(tmp_path)
    original = (config / 'player.toml').read_bytes()
    update(BASE, state, command(state))
    update(BASE, state, command(state, action='reset'))
    assert read_overrides(state) == ({}, 'none')
    assert not (state / FILE_NAME).exists()
    assert (config / 'player.toml').read_bytes() == original
    assert merged(BASE, state_dir=state)[0] == BASE


def test_stale_revision_is_rejected(tmp_path):
    _, state = configured(tmp_path)
    first = command(state)
    update(BASE, state, first)
    with pytest.raises(SettingsConflict):
        update(BASE, state, first)
    assert read_overrides(state)[0]['streaming.max_buffer_mib'] == 64


@pytest.mark.parametrize('values', [
    {}, {'sonos.use_queue': 'false', 'playback.continue': False, 'streaming.max_buffer_mib': 64},
    {'sonos.use_queue': False, 'playback.continue': False, 'streaming.max_buffer_mib': True},
    {'sonos.use_queue': False, 'playback.continue': False, 'streaming.max_buffer_mib': 513},
    {'sonos.use_queue': False, 'playback.continue': False, 'streaming.max_buffer_mib': 15},
    {'sonos.use_queue': False, 'playback.continue': False, 'streaming.max_buffer_mib': 64, 'secret': 1},
])
def test_refuses_invalid_or_unknown_fields(tmp_path, values):
    _, state = configured(tmp_path)
    with pytest.raises(ValueError):
        update(BASE, state, command(state, values=values))
    assert not (state / FILE_NAME).exists()


def test_saved_identical_to_base_removes_overlay(tmp_path):
    _, state = configured(tmp_path)
    update(BASE, state, command(state))
    unchanged = {'sonos.use_queue': True, 'playback.continue': True,
                 'streaming.max_buffer_mib': 16}
    assert update(BASE, state, command(state, values=unchanged)) == 'none'
    assert not (state / FILE_NAME).exists()


def test_corrupt_or_unknown_state_does_not_override_player(tmp_path):
    _, state = configured(tmp_path)
    path = state / FILE_NAME
    for raw in (b'{invalid', b'{"version":2,"values":{}}',
                b'{"version":1,"values":{"unknown":"password"}}'):
        path.write_bytes(raw)
        new, revision = merged(BASE, state_dir=state)
        assert new == BASE
        assert revision == 'invalid'
        with pytest.raises(ValueError):
            update(BASE, state, command(state))
        assert update(BASE, state, command(state, action='reset')) == 'none'


def test_rejects_symlink_state_and_invalid_base(tmp_path):
    _, state = configured(tmp_path)
    target = state / 'protected'
    target.write_text('retain')
    (state / FILE_NAME).symlink_to(target)
    with pytest.raises(OSError):
        update(BASE, state, command(state, action='reset'))
    assert target.read_text() == 'retain'
    with pytest.raises(ValueError):
        base_values({'streaming': {'max_buffer_mib': 0}})
    assert not (state / FILE_NAME).is_file() or (state / FILE_NAME).is_symlink()


def test_uses_runtime_default_true_for_missing_optional_booleans():
    data = {'streaming': {'max_buffer_mib': 16}}
    assert base_values(data) == {'sonos.use_queue': True,
                                 'playback.continue': True,
                                 'streaming.max_buffer_mib': 16}


def test_http_staging_and_running_settings_are_independent(tmp_path, monkeypatch):
    """POST writes state only; it never sends a command to the player."""
    from http.server import ThreadingHTTPServer
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from sonoscdplayer.web.server import make_handler
    from sonoscdplayer.web.state import WebPreferences

    config, state = configured(tmp_path)
    monkeypatch.setenv('SONOSCDPLAYER_CONFIG_DIR', str(config))
    player_toml = (config / 'player.toml').read_bytes()

    class FakePlayer:
        def __init__(self):
            self.commands = []
        def request(self, message, *, timeout=2):
            self.commands.append(message)
            assert message == {'command': 'settings-status'}
            return {'ok': True, 'settings': {'sonos.use_queue': True,
                                             'playback.continue': True,
                                             'streaming.max_buffer_mib': 16}}

    player = FakePlayer()
    http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(player, WebPreferences(state)))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{http.server_port}'
    try:
        def get(route):
            with urlopen(base + route, timeout=3) as response:
                return json.load(response)
        def post(message, headers=None):
            req = Request(base + '/api/settings/playback', data=json.dumps(message).encode(),
                          headers={'Content-Type': 'application/json', **(headers or {})})
            try:
                with urlopen(req, timeout=3) as response:
                    return response.status, json.load(response)
            except HTTPError as exc:
                return exc.code, json.loads(exc.read())

        before = get('/api/settings')
        assert before['playback']['revision'] == 'none'
        assert get('/api/settings/runtime')['values']['streaming.max_buffer_mib'] == 16
        calls = len(player.commands)
        command_body = command(state)
        status, saved = post(command_body)
        assert status == 200 and saved['ok'] is True
        assert len(player.commands) == calls  # Saving never calls player.
        assert (config / 'player.toml').read_bytes() == player_toml
        after = get('/api/settings')
        overrides = {row['key']: row for row in after['settings']}
        assert overrides['streaming.max_buffer_mib']['value'] == 64
        assert overrides['streaming.max_buffer_mib']['origin_file'] == FILE_NAME
        assert get('/api/settings/runtime')['values']['streaming.max_buffer_mib'] == 16
        assert post(command_body)[0] == 409  # stale revision
        invalid = {'action': 'save', 'revision': saved['revision'], 'values': {
            'sonos.use_queue': 'false', 'playback.continue': False,
            'streaming.max_buffer_mib': 64}}
        assert post(invalid)[0] == 400
        assert post(command(state, action='reset'), {'Origin': 'https://evil.example'})[0] == 403
        assert post(command(state, action='reset'))[0] == 200
        assert not (state / FILE_NAME).exists()
        assert (config / 'player.toml').read_bytes() == player_toml
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()
