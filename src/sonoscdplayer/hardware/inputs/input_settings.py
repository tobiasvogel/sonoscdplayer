"""Bounded startup-only input tuning; pin and electrical configuration stay in TOML.

The web editor may later stage input-settings.json; reading it must never
silently turn a corrupt document into another input configuration.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from sonoscdplayer.system.state_files import default_state_dir

FILE_NAME = 'input-settings.json'
MAX_BYTES = 4096
# These are non-electrical, restart-only settings. Never accept pin/source,
# pull, active level or power-hold edits from the browser.
BOUNDS = {
    'gpio.poll_interval_ms': (1, 1000),
    'volume.repeat_delay_ms': (100, 5000),
    'volume.repeat_interval_ms': (50, 2000),
    'encoder_input.button_debounce_ms': (0, 250),
}
STEPS = 'encoder_input.steps_per_detent'
INVERT = 'encoder_input.invert_direction'
FIELDS = frozenset(BOUNDS) | {STEPS, INVERT}


def valid(key, value):
    if key in BOUNDS:
        low, high = BOUNDS[key]
        return type(value) is int and low <= value <= high
    if key == STEPS:
        return type(value) is int and value in (1, 2, 4)
    return key == INVERT and type(value) is bool


def read_settings(directory=None):
    """Return (validated changes, revision); refuse malformed saved state."""
    root = Path(directory) if directory is not None else default_state_dir()
    path = root / FILE_NAME
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return {}, 'none'
    except OSError as exc:
        raise ValueError('input settings unreadable') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise ValueError('input settings file invalid')
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            raw = stream.read(MAX_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(raw) > MAX_BYTES:
        raise ValueError('input settings too large')
    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('input settings invalid JSON') from exc
    if (type(data) is not dict or set(data) != {'version', 'values'} or
            type(data['version']) is not int or data['version'] != 1 or
            type(data['values']) is not dict or
            any(key not in FIELDS or not valid(key, value)
                for key, value in data['values'].items())):
        raise ValueError('input settings invalid values')
    return dict(data['values']), sha256(raw).hexdigest()


def apply_settings(config, changes):
    """Apply only validated timing/direction settings to the parsed Config."""
    if type(changes) is not dict or any(not valid(k, v) for k, v in changes.items()):
        raise ValueError('invalid input settings overlay')
    cfg = config
    if 'gpio.poll_interval_ms' in changes:
        cfg = replace(cfg, poll_interval_ms=changes['gpio.poll_interval_ms'])
    if 'volume.repeat_delay_ms' in changes:
        cfg = replace(cfg, volume_repeat_delay_ms=changes['volume.repeat_delay_ms'])
    if 'volume.repeat_interval_ms' in changes:
        cfg = replace(cfg, volume_repeat_interval_ms=changes['volume.repeat_interval_ms'])
    encoder = cfg.encoder
    if 'encoder_input.button_debounce_ms' in changes:
        encoder = replace(encoder, switch=replace(
            encoder.switch, debounce_ms=changes['encoder_input.button_debounce_ms']))
    if STEPS in changes:
        encoder = replace(encoder, steps_per_detent=changes[STEPS])
    if INVERT in changes:
        encoder = replace(encoder, invert_direction=changes[INVERT])
    return replace(cfg, encoder=encoder)


def merged_settings(config, directory=None):
    changes, _ = read_settings(directory)
    return apply_settings(config, changes)
