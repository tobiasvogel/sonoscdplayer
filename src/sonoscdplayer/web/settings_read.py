"""WEB-05a: bounded, read-only view of installed configuration and owned UI state.

No arbitrary config keys, filenames, credentials or raw JSON documents leave this
module. A stored preference is *not* claimed to be applied by a running service.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tomllib

from sonoscdplayer.system import config_paths
from sonoscdplayer.system.state_files import default_state_dir
from sonoscdplayer.ui.i18n.translator import available_languages
from sonoscdplayer.ui.language_state import UiState
from sonoscdplayer.player.playback_settings import FIELDS as PLAYBACK_FIELDS, read_overrides, base_values

MAX_CONFIG_BYTES = 65536
MAX_STATE_BYTES = 16384


class SettingsUnavailable(ValueError):
    """A configuration file cannot be represented safely or faithfully."""


# Explicitly allowlisted non-secret options. Other keys (including future
# credentials) are never returned through HTTP. Identifiers are technical, not
# translated human-readable strings.
PLAYER_FIELDS = (
    'streaming.max_buffer_mib',
    'metadata.enabled', 'metadata.read_cd_text', 'metadata.online_lookup',
    'metadata.auto_match', 'metadata.matching.confidence_threshold',
    'metadata.matching.minimum_margin', 'metadata.matching.minimum_track_titles',
    'metadata.matching.rule_languages', 'metadata.matching.soft_suffixes',
    'metadata.matching.protected_suffixes',
    'metadata.musicbrainz.enabled', 'metadata.musicbrainz.timeout_seconds',
    'sonos.album_prefix', 'sonos.use_queue', 'playback.continue',
    'audio_startup.prebuffer_before_sonos', 'audio_startup.pcm_timeout_seconds',
    'drive_power.enabled', 'drive_power.strategy',
    'drive_power.keepalive_interval_seconds', 'drive_power.idle_spin_down_seconds',
    'service.shutdown_policy', 'service.experimental_safe_screen',
    'gpio.warnings',
    'led_outputs.pwm_hz', 'led_outputs.tick_ms', 'led_outputs.brightness',
    'led_outputs.active_high', 'led_outputs.error_hold_ms',
    'led_outputs.ambient.enabled', 'led_outputs.ambient.timeout_ms',
    'led_outputs.ambient.poll_ms', 'led_outputs.ambient.stale_ms',
    'led_outputs.ambient.smoothing_ms', 'led_outputs.ambient.min_brightness',
    'led_outputs.ambient.max_brightness', 'led_outputs.ambient.fallback_brightness',
    'led_outputs.ambient.lux_dark', 'led_outputs.ambient.lux_bright',
    'display.controller', 'display.interface', 'display.spi_bus',
    'display.spi_device', 'display.dc_pin', 'display.rst_pin',
)
UI_FIELDS = (
    'ui.language', 'ui.cover.mode', 'ui.cover.max_dimension_px',
    'ui.cover.jpeg_quality', 'ui.cover.budget_mb', 'ui.text_input.extra_characters',
)
INTERFACE_FIELDS = (
    'gpio.numbering', 'gpio.poll_interval_ms',
    'buttons.power', 'buttons.connect', 'buttons.eject', 'buttons.previous',
    'buttons.stop', 'buttons.playpause', 'buttons.skip', 'buttons.volume_up',
    'buttons.volume_dn', 'buttons.speaker_select',
    'power.long_press_ms', 'volume.repeat_delay_ms', 'volume.repeat_interval_ms',
    'encoder.encoder_a', 'encoder.encoder_b', 'encoder.encoder_sw',
    'input_defaults.pull', 'input_defaults.active_level',
    'input_defaults.debounce_ms', 'encoder_input.pull',
    'encoder_input.active_level', 'encoder_input.button_debounce_ms',
    'encoder_input.steps_per_detent', 'encoder_input.invert_direction',
)
# These defaults are explicitly verified in the existing runtime loaders.
KNOWN_DEFAULTS = {
    'sonos.use_queue': True,
    'playback.continue': True,
    'ui.language': 'de',
    'ui.cover.mode': 'auto',
    'web.appearance.theme': 'auto',
}


def _document(path: Path, *, limit: int, required: bool = False):
    """Read only regular, bounded, non-symlink JSON/TOML source files."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        if required:
            raise SettingsUnavailable('required configuration unavailable') from None
        return {}
    except OSError as exc:
        raise SettingsUnavailable('configuration unavailable') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= limit:
            raise SettingsUnavailable('invalid configuration file')
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise SettingsUnavailable('configuration too large')
        if path.suffix == '.toml':
            parsed = tomllib.loads(raw.decode('utf-8'))
        else:
            parsed = json.loads(raw.decode('utf-8'))
        if type(parsed) is not dict:
            raise SettingsUnavailable('invalid configuration document')
        return parsed
    except (ValueError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SettingsUnavailable('invalid configuration document') from exc
    finally:
        if fd >= 0:
            os.close(fd)


# Type categories match the installed configuration examples and runtime rules.
BOOLEAN_FIELDS = frozenset(('metadata.enabled', 'metadata.read_cd_text', 'metadata.online_lookup', 'metadata.auto_match', 'metadata.musicbrainz.enabled', 'sonos.use_queue', 'playback.continue', 'audio_startup.prebuffer_before_sonos', 'drive_power.enabled', 'service.experimental_safe_screen', 'gpio.warnings', 'led_outputs.active_high', 'led_outputs.ambient.enabled', 'encoder_input.invert_direction'))
FLOAT_FIELDS = frozenset(('led_outputs.brightness', 'led_outputs.ambient.min_brightness', 'led_outputs.ambient.max_brightness', 'led_outputs.ambient.fallback_brightness', 'led_outputs.ambient.lux_dark', 'led_outputs.ambient.lux_bright'))
LIST_FIELDS = frozenset((
    'metadata.matching.rule_languages', 'metadata.matching.soft_suffixes',
    'metadata.matching.protected_suffixes',
))
STRING_FIELDS = frozenset((
    'sonos.album_prefix', 'drive_power.strategy', 'service.shutdown_policy',
    'display.controller', 'display.interface', 'ui.language', 'ui.cover.mode',
    'ui.text_input.extra_characters', 'gpio.numbering', 'input_defaults.pull',
    'input_defaults.active_level', 'encoder_input.pull', 'encoder_input.active_level',
))


ENUMS = {
    'drive_power.strategy': ('off', 'start', 'keepalive', 'power_condition', 'auto'),
    'service.shutdown_policy': ('stop_owned', 'leave_playing'),
    'ui.cover.mode': ('auto', 'on', 'off'),
    'gpio.numbering': ('BCM',),
    'input_defaults.pull': ('off', 'up', 'down'),
    'encoder_input.pull': ('off', 'up', 'down'),
    'input_defaults.active_level': ('high', 'low'),
    'encoder_input.active_level': ('high', 'low'),
}


def _valid_config(identifier, value):
    """Never present malformed declared values as if they were active."""
    if identifier == 'ui.language':
        return type(value) is str and value in available_languages()
    if identifier in ENUMS:
        return type(value) is str and value in ENUMS[identifier]
    if identifier in BOOLEAN_FIELDS:
        return type(value) is bool
    if identifier in FLOAT_FIELDS:
        return type(value) in (int, float) and _safe(value) is not None
    if identifier in LIST_FIELDS:
        return type(value) is list and _safe(value) is not None
    if identifier in STRING_FIELDS:
        return type(value) is str and _safe(value) is not None
    return type(value) is int and _safe(value) is not None


def _state_document(path):
    """Invalid or absent state is ignored, as in the existing UI state loader."""
    try:
        return _document(path, limit=MAX_STATE_BYTES)
    except SettingsUnavailable:
        return {}


def _value(document, identifier):
    item = document
    for part in identifier.split('.'):
        if type(item) is not dict or part not in item:
            return None
        item = item[part]
    return item


def _safe(value):
    if type(value) is bool:
        return value
    if type(value) is int and -100000 <= value <= 1000000:
        return value
    if type(value) is float and -100000 <= value <= 1000000:
        return value
    if type(value) is str and len(value) <= 256 and all(c.isprintable() for c in value):
        return value
    if type(value) is list and len(value) <= 100 and all(
            type(item) is str and len(item) <= 100 and all(c.isprintable() for c in item)
            for item in value):
        return value
    return None


def _row(filename, identifier, base, *, override=None, has_override=False):
    base = _safe(base)
    effective = _safe(override) if has_override else base
    source = 'state' if has_override else ('config' if base is not None else 'unknown')
    if not has_override and base is None and identifier in KNOWN_DEFAULTS:
        effective = KNOWN_DEFAULTS[identifier]
        source = 'default'
    origin = ('ui.json' if identifier == 'ui.language' and has_override
              else filename if source in ('config', 'state') else None)
    return {'file': filename, 'key': identifier, 'base': base,
            'value': effective, 'source': source, 'origin_file': origin,
            'stored_override': has_override,
            'runtime_verified': False}


def settings_snapshot(*, config_root=None, state_root=None):
    """Combine known settings from three TOMLs and two *existing* state files.

    ui.json only overrides ui.language; web.json owns the independent theme.
    No other /var state is mistakenly treated as a configuration overlay.
    """
    root = Path(config_root) if config_root is not None else config_paths.config_dir()
    state = Path(state_root) if state_root is not None else default_state_dir()
    player = _document(root / 'player.toml', limit=MAX_CONFIG_BYTES, required=True)
    interface = _document(root / 'interface.toml', limit=MAX_CONFIG_BYTES)
    ui = _document(root / 'ui.toml', limit=MAX_CONFIG_BYTES)
    display_state = _state_document(state / 'ui.json')
    language_revision = UiState(state, log=lambda *_: None).revision()
    web_state = _state_document(state / 'web.json')
    playback_overrides, playback_revision = read_overrides(state)
    # Invalid base values are shown as invalid, not silently concealed by a
    # stored override. No writes are performed by this read-only endpoint.
    playable = True
    try:
        base_values(player)
    except ValueError:
        playable = False

    saved_language = (display_state.get('language')
                      if type(display_state.get('version')) is int and display_state['version'] == 1
                      else None)
    if type(saved_language) is not str or saved_language not in available_languages():
        saved_language = None
    theme = _value(web_state, 'appearance.theme')
    if type(theme) is not str or theme not in ('auto', 'light', 'dark'):
        theme = None

    rows = []
    invalid_keys = []
    for filename, document, names in (
        ('player.toml', player, PLAYER_FIELDS),
        ('ui.toml', ui, UI_FIELDS),
        ('interface.toml', interface, INTERFACE_FIELDS),
    ):
        for identifier in names:
            base = _value(document, identifier)
            if base is not None and not _valid_config(identifier, base):
                # An invalid *individual* setting must never hide otherwise
                # valid playback settings. Never publish its original value.
                invalid_keys.append(identifier)
                if identifier == 'ui.language' and saved_language is not None:
                    rows.append(_row(filename, identifier, None,
                                     override=saved_language, has_override=True))
                else:
                    rows.append({'file': filename, 'key': identifier, 'base': None,
                                 'value': None, 'source': 'invalid',
                                 'origin_file': filename, 'stored_override': False,
                                 'runtime_verified': False})
                continue
            if base is None and identifier not in KNOWN_DEFAULTS and not (
                    identifier == 'ui.language' and saved_language is not None):
                continue
            if identifier == 'ui.language' and saved_language is not None:
                rows.append(_row(filename, identifier, base, override=saved_language,
                                 has_override=True))
            elif filename == 'player.toml' and identifier in PLAYBACK_FIELDS and playable and identifier in playback_overrides:
                rows.append(_row(filename, identifier, base,
                                 override=playback_overrides[identifier], has_override=True))
                rows[-1]['origin_file'] = 'player-settings.json'
            else:
                rows.append(_row(filename, identifier, base))
    rows.append(_row('web.json', 'web.appearance.theme', None,
                     override=theme, has_override=theme is not None))
    return {'ok': True, 'settings': rows, 'read_only': True,
            'runtime_verified': False, 'invalid_keys': invalid_keys,
            'display': {'language_revision': language_revision},
            'playback': {'editable': playable, 'revision': playback_revision,
                         'runtime': None, 'restart_required': None}}
