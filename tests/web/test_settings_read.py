"""WEB-05a: no secret exposure, no writes and honest config/state provenance."""
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from sonoscdplayer.web.settings_read import SettingsUnavailable, settings_snapshot


def sample_files(tmp_path):
    config = tmp_path / 'etc'
    state = tmp_path / 'var'
    config.mkdir(); state.mkdir()
    (config / 'player.toml').write_text(
        '[sonos]\nuse_queue = false\nalbum_prefix = "CD: "\n'
        '[playback]\ncontinue = true\n[streaming]\nmax_buffer_mib = 64\n'
        '[metadata]\nenabled = true\napi_password = "sensitive"\n',
        encoding='utf-8')
    (config / 'ui.toml').write_text('[ui]\nlanguage = "de"\n[ui.cover]\nmode = "off"\n', encoding='utf-8')
    (config / 'interface.toml').write_text('[encoder_input]\ninvert_direction = true\n', encoding='utf-8')
    (state / 'ui.json').write_text('{"version":1,"language":"en"}', encoding='utf-8')
    (state / 'web.json').write_text('{"appearance":{"theme":"dark"},"secret":"hidden"}', encoding='utf-8')
    return config, state


def by_key(snapshot, key):
    return next(item for item in snapshot['settings'] if item['key'] == key)


def test_merge_base_and_existing_owned_state(tmp_path):
    config, state = sample_files(tmp_path)
    before = {p: p.read_bytes() for p in (*config.iterdir(), *state.iterdir())}
    snapshot = settings_snapshot(config_root=config, state_root=state)
    assert snapshot['read_only'] and not snapshot['runtime_verified']
    assert by_key(snapshot, 'ui.language') == {
        'file': 'ui.toml', 'key': 'ui.language', 'base': 'de',
        'value': 'en', 'source': 'state', 'origin_file': 'ui.json', 'stored_override': True,
        'runtime_verified': False}
    assert by_key(snapshot, 'web.appearance.theme')['value'] == 'dark'
    assert by_key(snapshot, 'web.appearance.theme')['source'] == 'state'
    assert by_key(snapshot, 'sonos.use_queue')['value'] is False
    assert by_key(snapshot, 'sonos.use_queue')['source'] == 'config'
    assert by_key(snapshot, 'encoder_input.invert_direction')['value'] is True
    assert by_key(snapshot, 'streaming.max_buffer_mib')['value'] == 64
    assert 'sensitive' not in json.dumps(snapshot)
    assert 'hidden' not in json.dumps(snapshot)
    assert '/etc/' not in json.dumps(snapshot)
    assert all(p.read_bytes() == value for p, value in before.items())


def test_missing_optional_and_invalid_state_never_invents_overrides(tmp_path):
    config, state = sample_files(tmp_path)
    (config / 'ui.toml').unlink()
    (config / 'interface.toml').unlink()
    (state / 'ui.json').write_text('{broken', encoding='utf-8')
    (state / 'web.json').write_text('{"appearance":{"theme":"unknown"}}', encoding='utf-8')
    # Corrupt state is ignored for a read-only view rather than blocking playback.
    snap = settings_snapshot(config_root=config, state_root=state)
    assert by_key(snap, 'ui.language')['value'] == 'de'
    assert by_key(snap, 'ui.language')['source'] == 'default'
    assert by_key(snap, 'web.appearance.theme')['source'] == 'default'
    assert by_key(snap, 'playback.continue')['source'] == 'config'
    assert not any(x['file'] == 'interface.toml' for x in snap['settings'])


def test_invalid_toml_and_symlink_are_refused(tmp_path):
    config, state = sample_files(tmp_path)
    player = config / 'player.toml'
    player.write_text('[broken', encoding='utf-8')
    with pytest.raises(SettingsUnavailable):
        settings_snapshot(config_root=config, state_root=state)
    player.unlink()
    player.symlink_to(config / 'ui.toml')
    with pytest.raises(SettingsUnavailable):
        settings_snapshot(config_root=config, state_root=state)


def test_invalid_declared_field_cannot_be_misrepresented(tmp_path):
    config, state = sample_files(tmp_path)
    player = config / 'player.toml'
    player.write_text('[playback]\ncontinue = "maybe"\n', encoding='utf-8')
    result = settings_snapshot(config_root=config, state_root=state)
    assert by_key(result, 'playback.continue')['source'] == 'invalid'
    assert by_key(result, 'playback.continue')['value'] is None
    assert by_key(result, 'playback.continue')['base'] is None
    assert result['invalid_keys'] == ['playback.continue']
    assert by_key(result, 'sonos.use_queue')['source'] == 'default'
    assert 'maybe' not in json.dumps(result)


def test_broken_unrelated_setting_does_not_hide_playback_values(tmp_path):
    config, state = sample_files(tmp_path)
    player = config / 'player.toml'
    player.write_text(player.read_text(encoding='utf-8') +
                      '[led_outputs]\nbrightness = "not-a-number"\n', encoding='utf-8')
    result = settings_snapshot(config_root=config, state_root=state)
    assert by_key(result, 'streaming.max_buffer_mib')['value'] == 64
    assert by_key(result, 'sonos.use_queue')['value'] is False
    assert by_key(result, 'playback.continue')['value'] is True
    assert by_key(result, 'led_outputs.brightness')['source'] == 'invalid'
    assert result['invalid_keys'] == ['led_outputs.brightness']
    assert 'not-a-number' not in json.dumps(result)


def test_valid_saved_language_wins_even_if_toml_language_invalid(tmp_path):
    config, state = sample_files(tmp_path)
    (config / 'ui.toml').write_text('[ui]\nlanguage = "not-supported"\n', encoding='utf-8')
    result = settings_snapshot(config_root=config, state_root=state)
    assert by_key(result, 'ui.language')['value'] == 'en'
    assert by_key(result, 'ui.language')['source'] == 'state'
    assert by_key(result, 'ui.language')['base'] is None
    assert result['invalid_keys'] == ['ui.language']
    assert 'not-supported' not in json.dumps(result)


def test_missing_required_player_document_is_still_rejected(tmp_path):
    config, state = sample_files(tmp_path)
    (config / 'player.toml').unlink()
    with pytest.raises(SettingsUnavailable):
        settings_snapshot(config_root=config, state_root=state)


def test_http_settings_is_read_only_and_no_player_rpc(tmp_path, monkeypatch):
    from sonoscdplayer.web.server import make_handler
    from sonoscdplayer.web.state import WebPreferences
    config, state = sample_files(tmp_path)
    monkeypatch.setenv('SONOSCDPLAYER_CONFIG_DIR', str(config))
    class NoPlayerCalls:
        def request(self, *args, **kwargs):
            raise AssertionError('settings read must never contact player')
    http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(NoPlayerCalls(), WebPreferences(state)))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{http.server_port}/api/settings'
        with urlopen(url, timeout=3) as response:
            assert response.status == 200
            payload = json.load(response)
            assert by_key(payload, 'ui.language')['value'] == 'en'
            assert response.headers['Cache-Control'] == 'no-store'
        player = config / 'player.toml'
        player.write_text(player.read_text(encoding='utf-8') +
                          '[led_outputs]\nbrightness = "invalid"\n', encoding='utf-8')
        with urlopen(url, timeout=3) as response:
            partial = json.load(response)
        assert by_key(partial, 'sonos.use_queue')['value'] is False
        assert partial['invalid_keys'] == ['led_outputs.brightness']
        player.write_text('{invalid', encoding='utf-8')
        with pytest.raises(HTTPError) as error:
            urlopen(url, timeout=3)
        assert error.value.code == 503
        response = json.loads(error.value.read())
        assert response['ok'] is False
        assert 'invalid' not in response['error'].lower()
    finally:
        http.shutdown(); http.server_close(); thread.join(timeout=3)
        assert not thread.is_alive()
