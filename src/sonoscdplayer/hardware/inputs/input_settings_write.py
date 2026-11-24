"""WEB-05d2b: atomic, revision-checked, startup-only input tuning writer.

Only the six explicitly allowed non-electrical fields may be written.  Never
modify interface.toml, restart the button service, or access GPIO.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from sonoscdplayer.hardware.inputs.input_settings import (
    FILE_NAME, FIELDS, apply_settings, read_settings, valid,
)

_LOCK_FILE = '.input-settings.lock'
_REVISION = re.compile(r'(?:none|invalid|[0-9a-f]{64})\Z')


class InputSettingsConflict(ValueError):
    """The input settings changed after the browser loaded them."""


@contextmanager
def _locked(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(root / _LOCK_FILE,
                         os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError('invalid input settings lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def update_input_settings(directory, base_config, request):
    """Commit a complete six-field snapshot, or remove all overrides.

    The caller supplies the fully validated production config; values are
    overlaid on it only after strict allowlist/type checks.  A stale revision
    fails before touching disk.  The reader and writer share the exact format.
    """
    if type(request) is not dict or set(request) != {'action', 'revision', 'values'}:
        raise ValueError('invalid input settings payload')
    action = request['action']
    revision = request['revision']
    values = request['values']
    if (action not in ('save', 'reset') or type(action) is not str or
            type(revision) is not str or _REVISION.fullmatch(revision) is None or
            type(values) is not dict):
        raise ValueError('invalid input settings request')
    if action == 'save' and (set(values) != FIELDS or
                             any(not valid(key, value) for key, value in values.items())):
        raise ValueError('invalid input settings values')
    if action == 'reset' and values:
        raise ValueError('reset must not include values')
    # Validate compatibility with the existing Config before creating state.
    if action == 'save':
        apply_settings(base_config, values)
    root = Path(directory)
    with _locked(root):
        try:
            _current, existing_revision = read_settings(root)
        except ValueError:
            # A corrupt overlay must never be applied, but a deliberate reset
            # can recover it. Ordinary saves remain forbidden.
            existing_revision = 'invalid'
        if existing_revision != revision:
            raise InputSettingsConflict('input settings changed')
        if existing_revision == 'invalid' and action != 'reset':
            raise ValueError('invalid saved input settings')
        path = root / FILE_NAME
        if path.is_symlink():
            raise OSError('invalid input settings path')
        if action == 'reset':
            path.unlink(missing_ok=True)
        else:
            payload = (json.dumps({'version': 1, 'values': values}, sort_keys=True,
                                  separators=(',', ':')) + '\n').encode('utf-8')
            fd, temporary = tempfile.mkstemp(prefix='.input-settings-', dir=root)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return read_settings(root)[1]
