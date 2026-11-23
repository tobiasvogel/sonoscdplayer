"""WEB-05b: validated, atomic state overlay for three playback settings.

The administrator-owned player.toml is never modified. The player takes a
snapshot of the overlay at startup; the web service may only stage values.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile

from sonoscdplayer.system.state_files import default_state_dir

FILE_NAME = 'player-settings.json'
FIELDS = ('sonos.use_queue', 'playback.continue', 'streaming.max_buffer_mib')
VERSION = 1
MAX_BYTES = 4096


class SettingsConflict(ValueError):
    """A second client changed the settings since they were read."""


def valid(field, value):
    if field in FIELDS[:2]:
        return type(value) is bool
    if field == FIELDS[2]:
        return type(value) is int and 16 <= value <= 512
    return False


def _base_value(config, field):
    section, key = field.split('.')
    group = config.get(section)
    if not isinstance(group, dict):
        if field in FIELDS[:2] and group is None:
            return True  # The documented runtime default.
        raise ValueError('invalid playback configuration')
    value = group.get(key, True if field in FIELDS[:2] else None)
    if not valid(field, value):
        raise ValueError('invalid playback configuration')
    return value


def base_values(config):
    if type(config) is not dict:
        raise ValueError('invalid playback configuration')
    return {field: _base_value(config, field) for field in FIELDS}


def _decode(raw):
    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):
        return None
    if type(data) is not dict or set(data) != {'version', 'values'} or type(data['version']) is not int or data['version'] != VERSION:
        return None
    values = data['values']
    if type(values) is not dict or any(not valid(key, value) for key, value in values.items()):
        return None
    return values


def _read(state_dir):
    """Read only a bounded, regular file. Invalid or missing state is ignored."""
    path = Path(state_dir) / FILE_NAME
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return {}, 'none'
    except OSError:
        return {}, 'invalid'
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            return {}, 'invalid'
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return {}, 'invalid'
    except OSError:
        return {}, 'invalid'
    finally:
        if fd >= 0:
            os.close(fd)
    values = _decode(data)
    if values is None:
        return {}, 'invalid'
    revision = sha256(data).hexdigest()
    return values, revision


def read_overrides(state_dir=None):
    """The valid stored delta and an opaque revision for optimistic locking."""
    return _read(default_state_dir() if state_dir is None else state_dir)


def merged(config, *, state_dir=None):
    """Apply only recognized settings, returning a new dict and stored revision."""
    base = base_values(config)
    overlay, revision = read_overrides(state_dir)
    result = deepcopy(config)
    for field in FIELDS:
        section, key = field.split('.')
        result.setdefault(section, {})[key] = overlay.get(field, base[field])
    return result, revision


def _atomic_write(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError('invalid settings state path')
    fd, temporary = tempfile.mkstemp(prefix='.player-settings-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def update(config, state_dir, request):
    """Atomically stage a full three-field selection or reset all overrides.

    The caller serializes updates using a lock. Revision conflicts are
    rejected; a bad base config is not silently replaced by new state.
    """
    if type(request) is not dict or set(request) != {'action', 'revision', 'values'}:
        raise ValueError('invalid playback settings request')
    action, revision, values = request['action'], request['revision'], request['values']
    if action not in ('save', 'reset') or type(revision) is not str or len(revision) > 64:
        raise ValueError('invalid playback settings action')
    if action == 'reset':
        if values != {}:
            raise ValueError('reset must not provide values')
    elif type(values) is not dict or set(values) != set(FIELDS) or any(not valid(k, v) for k, v in values.items()):
        raise ValueError('invalid playback settings values')
    base = base_values(config)
    current, current_revision = read_overrides(state_dir)
    if revision != current_revision:
        raise SettingsConflict('settings changed by another client')
    if current_revision == 'invalid':
        # Never clobber a corrupt state file on an ordinary save.
        if action != 'reset':
            raise ValueError('invalid stored settings; reset required')
    path = Path(state_dir) / FILE_NAME
    if action == 'reset':
        if path.is_symlink():
            raise OSError('invalid settings state path')
        path.unlink(missing_ok=True)
    else:
        delta = {key: values[key] for key in FIELDS if values[key] != base[key]}
        if delta:
            payload = (json.dumps({'version': VERSION, 'values': delta},
                                  ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
            _atomic_write(path, payload)
        else:
            if path.is_symlink():
                raise OSError('invalid settings state path')
            path.unlink(missing_ok=True)
    return read_overrides(state_dir)[1]
