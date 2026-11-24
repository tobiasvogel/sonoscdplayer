"""WEB-05d2b: no GPIO, unsafe controls or config file writes."""
import json
from http.server import ThreadingHTTPServer
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from sonoscdplayer.hardware.inputs.event_engine import (
    ButtonConfig, Config, EncoderConfig,
)
from sonoscdplayer.hardware.inputs.input_settings import FIELDS, read_settings
from sonoscdplayer.hardware.inputs.input_settings_write import (
    InputSettingsConflict, update_input_settings,
)
from sonoscdplayer.web.server import make_handler
from sonoscdplayer.web.state import WebPreferences


def base():
    encoder = EncoderConfig(6, 26, ButtonConfig(5, 'off', 'low', 0), 'off', 4, False)
    return Config({'stop': ButtonConfig(18, 'off', 'high', 0)}, encoder, 2)


def values():
    return {'gpio.poll_interval_ms': 3,
            'volume.repeat_delay_ms': 800,
            'volume.repeat_interval_ms': 200,
            'encoder_input.button_debounce_ms': 20,
            'encoder_input.steps_per_detent': 2,
            'encoder_input.invert_direction': True}


def test_revision_reset_and_invalid_inputs(tmp_path):
    assert set(values()) == FIELDS
    revision = update_input_settings(tmp_path, base(), {
        'action': 'save', 'revision': 'none', 'values': values()})
    assert len(revision) == 64
    assert read_settings(tmp_path) == (values(), revision)
    with pytest.raises(InputSettingsConflict):
        update_input_settings(tmp_path, base(), {
            'action': 'save', 'revision': 'none', 'values': values()})
    for invalid in ({**values(), 'buttons.stop': 1},
                    {**values(), 'encoder_input.steps_per_detent': True},
                    {k: v for k, v in values().items() if k != 'gpio.poll_interval_ms'}):
        with pytest.raises(ValueError):
            update_input_settings(tmp_path, base(), {
                'action': 'save', 'revision': revision, 'values': invalid})
    assert read_settings(tmp_path)[1] == revision
    assert update_input_settings(tmp_path, base(), {
        'action': 'reset', 'revision': revision, 'values': {}}) == 'none'
    assert not (tmp_path / 'input-settings.json').exists()


def test_refuse_symlink_and_corrupt_state(tmp_path):
    target = tmp_path / 'protected'
    target.write_text('original')
    path = tmp_path / 'input-settings.json'
    path.symlink_to(target)
    with pytest.raises(ValueError):
        update_input_settings(tmp_path, base(), {
            'action': 'save', 'revision': 'none', 'values': values()})
    assert target.read_text() == 'original'
    path.unlink()
    path.write_text('{broken')
    with pytest.raises(ValueError):
        update_input_settings(tmp_path, base(), {
            'action': 'save', 'revision': 'none', 'values': values()})


def test_web_endpoint_does_not_contact_player_or_modify_toml(tmp_path, monkeypatch):
    config = tmp_path / 'etc'
    state = tmp_path / 'state'
    config.mkdir()
    state.mkdir()
    (config / 'player.toml').write_text('[streaming]\nmax_buffer_mib=16\n')
    (config / 'interface.toml').write_text(
        '[buttons]\npower=17\nconnect=21\neject=20\nprevious=15\nstop=18\n'
        'playpause=14\nskip=16\nvolume_up=27\nvolume_dn=22\n'
        '[encoder]\nencoder_a=6\nencoder_b=26\nencoder_sw=5\n')
    original = (config / 'interface.toml').read_bytes()
    monkeypatch.setenv('SONOSCDPLAYER_CONFIG_DIR', str(config))
    class NoPlayer:
        def request(self, *_args, **_kwargs):
            raise AssertionError('settings must not contact the player')
    http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(NoPlayer(), WebPreferences(state)))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{http.server_port}'
    def get():
        with urlopen(url + '/api/settings', timeout=3) as response:
            return json.load(response)
    def post(payload):
        request = Request(url + '/api/settings/input', data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())
    try:
        initial = get()
        assert initial['input']['revision'] == 'none'
        assert initial['input']['editable'] is True
        status, result = post({'action': 'save', 'revision': 'none', 'values': values()})
        assert status == 200
        assert len(result['revision']) == 64
        assert get()['input']['revision'] == result['revision']
        assert post({'action': 'save', 'revision': 'none', 'values': values()})[0] == 409
        assert post({'action': 'save', 'revision': result['revision'],
                     'values': {**values(), 'buttons.stop': 3}})[0] == 400
        assert (config / 'interface.toml').read_bytes() == original
        assert post({'action': 'reset', 'revision': result['revision'], 'values': {}})[0] == 200
        assert get()['input']['revision'] == 'none'
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)
