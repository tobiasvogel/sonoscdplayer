"""Service-owned cover mode overlay; never modifies administrator ui.toml.

The mode is shared by the display and metadata worker, both of which read
it at startup. A staged change does not restart either service.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile

from sonoscdplayer.system.state_files import default_state_dir

FILE_NAME = 'cover-settings.json'
MODES = frozenset(('auto', 'on', 'off'))
MAX_BYTES = 1024


class CoverConflict(ValueError):
    """Another client changed the stored cover mode."""


def read_cover_mode(directory=None):
    """Return (mode override or None, revision); corrupted state is invalid."""
    root = Path(directory) if directory is not None else default_state_dir()
    path = root / FILE_NAME
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return None, 'none'
    except OSError:
        return None, 'invalid'
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            return None, 'invalid'
        with os.fdopen(fd, 'rb') as source:
            fd = -1
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None, 'invalid'
    except OSError:
        return None, 'invalid'
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError):
        return None, 'invalid'
    if (type(data) is not dict or set(data) != {'version', 'mode'} or
            type(data['version']) is not int or data['version'] != 1 or
            type(data['mode']) is not str or data['mode'] not in MODES):
        return None, 'invalid'
    return data['mode'], sha256(raw).hexdigest()


def save_cover_mode(directory, mode, revision):
    """Stage one validated mode with optimistic revision checking.

    The web service serializes calls with its settings lock. No other service
    writes this file. Reset to the TOML base is represented by mode=None.
    """
    if (mode is not None and (type(mode) is not str or mode not in MODES)):
        raise ValueError('invalid cover mode')
    if (type(revision) is not str or (revision not in ('none', 'invalid') and
            (len(revision) != 64 or any(c not in '0123456789abcdef' for c in revision)))):
        raise ValueError('invalid cover revision')
    root = Path(directory)
    current, current_revision = read_cover_mode(root)
    if current_revision != revision:
        raise CoverConflict('cover settings changed')
    if current_revision == 'invalid':
        raise ValueError('invalid saved cover state')
    path = root / FILE_NAME
    if path.is_symlink():
        raise OSError('invalid cover state path')
    if mode is None:
        path.unlink(missing_ok=True)
    else:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = (json.dumps({'version': 1, 'mode': mode}, sort_keys=True,
                          separators=(',', ':')) + '\n').encode('utf-8')
        fd, temporary = tempfile.mkstemp(prefix='.cover-settings-', suffix='.tmp', dir=root)
        try:
            with os.fdopen(fd, 'wb') as target:
                os.fchmod(target.fileno(), 0o600)
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return read_cover_mode(root)[1]
