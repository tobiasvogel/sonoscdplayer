"""WEB-05d2a: staged input tuning never changes wiring or TOML."""
import json
from dataclasses import replace

import pytest

from sonoscdplayer.hardware.inputs.event_engine import (
    Config, EncoderConfig, ButtonConfig,
)
from sonoscdplayer.hardware.inputs.input_settings import (
    FIELDS, apply_settings, read_settings, merged_settings,
)


def base_config():
    button = ButtonConfig(17, 'off', 'high', 15)
    encoder = EncoderConfig(6, 26, ButtonConfig(5, 'off', 'low', 0), 'off', 4, False)
    return Config({'stop': button}, encoder, 2, 3000, {}, 750, 250)


def test_no_overlay_preserves_all_runtime_settings(tmp_path):
    assert read_settings(tmp_path) == ({}, 'none')
    assert merged_settings(base_config(), tmp_path) == base_config()


def test_allowed_values_preserve_hardware_and_power_config(tmp_path):
    changes = {'gpio.poll_interval_ms': 5,
               'volume.repeat_delay_ms': 850,
               'volume.repeat_interval_ms': 150,
               'encoder_input.button_debounce_ms': 30,
               'encoder_input.steps_per_detent': 2,
               'encoder_input.invert_direction': True}
    (tmp_path / 'input-settings.json').write_text(json.dumps({'version': 1, 'values': changes}))
    found, revision = read_settings(tmp_path)
    assert found == changes and len(revision) == 64
    before = base_config()
    after = merged_settings(before, tmp_path)
    assert after.poll_interval_ms == 5
    assert after.volume_repeat_delay_ms == 850
    assert after.volume_repeat_interval_ms == 150
    assert after.encoder.steps_per_detent == 2
    assert after.encoder.invert_direction is True
    assert after.encoder.switch.debounce_ms == 30
    assert after.buttons == before.buttons
    assert (after.encoder.a, after.encoder.b, after.encoder.switch.pin) == (6, 26, 5)
    assert after.power_hold_ms == 3000


@pytest.mark.parametrize('changes', [
    {'buttons.stop': 19}, {'gpio.poll_interval_ms': 0},
    {'encoder_input.steps_per_detent': True},
    {'encoder_input.invert_direction': 1},
    {'input_defaults.pull': 'up'},
    {'power.long_press_ms': 3000},
])
def test_rejects_unsafe_or_invalid_values(tmp_path, changes):
    (tmp_path / 'input-settings.json').write_text(json.dumps({'version': 1, 'values': changes}))
    with pytest.raises(ValueError):
        read_settings(tmp_path)


def test_corrupt_and_symlink_state_is_refused(tmp_path):
    path = tmp_path / 'input-settings.json'
    path.write_text('{broken')
    with pytest.raises(ValueError):
        read_settings(tmp_path)
    path.unlink()
    target = tmp_path / 'other'
    target.write_text('{"version":1,"values":{}}')
    path.symlink_to(target)
    with pytest.raises(ValueError):
        read_settings(tmp_path)
