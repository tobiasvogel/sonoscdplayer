"""Small JSON state files in the state directory (D-216).

What is chosen during operation lives in ``/var/lib/sonoscdplayer`` (systemd
``StateDirectory=``, or ``$STATE_DIRECTORY``), one file per service, never in
the configuration files under ``/etc``. Files are written atomically with
mode 0600; a missing, unreadable or invalid file reads as ``None``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

DEFAULT_STATE_DIR = Path('/var/lib/sonoscdplayer')
MAX_BYTES = 16384


def default_state_dir():
    """systemd's StateDirectory= when running as a service, else the standard path."""
    value = os.environ.get('STATE_DIRECTORY', '').split(':')[0]
    return Path(value) if value else DEFAULT_STATE_DIR


def read_json(path, *, log=print):
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('file too large')
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        log(f'[state] {path.name} ignored: {exc}')
        return None


def write_json(path, data, *, log=print):
    """Atomically replace path; a failure only loses persistence."""
    path = Path(path)
    payload = json.dumps(data, indent=2, sort_keys=True) + '\n'
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.' + path.stem + '-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return True
    except OSError as exc:
        log(f'[state] {path.name} not saved: {exc}')
        return False


def remove(path, *, log=print):
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except OSError as exc:
        log(f'[state] {Path(path).name} not removed: {exc}')
        return False
