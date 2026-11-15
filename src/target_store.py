"""Remember the selected Sonos speakers across restarts (D-003, TGT-01).

The selection is kept in a small JSON file in the player's state directory
(``/var/lib/sonoscdplayer`` or systemd's ``$STATE_DIRECTORY``), never in the
configuration files under ``/etc``. Restoring a selection contacts no speaker
and never starts playback; PLAY performs the usual live checks (D-203).
A missing, unreadable or invalid file simply means "nothing selected".
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from sonos_group import UID_RE, normalize_members

DEFAULT_STATE_DIR = Path('/var/lib/sonoscdplayer')
FILE_NAME = 'target.json'
VERSION = 1
MAX_BYTES = 16384


def default_state_dir():
    """systemd's StateDirectory= when running as a service, else the standard path."""
    value = os.environ.get('STATE_DIRECTORY', '').split(':')[0]
    return Path(value) if value else DEFAULT_STATE_DIR


class TargetStore:
    def __init__(self, directory=None, *, log=print):
        self.path = Path(directory if directory is not None else default_state_dir()) / FILE_NAME
        self._log = log

    def load(self):
        """Return (coordinator_uid, members) or None. Never raises."""
        try:
            if self.path.is_symlink() or not self.path.is_file():
                return None
            if self.path.stat().st_size > MAX_BYTES:
                raise ValueError('file too large')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('version') != VERSION:
                raise ValueError('unknown format')
            uid = data.get('coordinator')
            if not isinstance(uid, str) or not UID_RE.fullmatch(uid):
                raise ValueError('invalid coordinator')
            members = tuple(sorted(normalize_members(uid, data.get('members', []))))
            return uid, members
        except (OSError, ValueError) as exc:
            self._log(f'[target] stored selection ignored: {exc}')
            return None

    def save(self, uid, members):
        """Atomically replace the file; a failure only loses persistence."""
        payload = json.dumps({'version': VERSION, 'coordinator': uid,
                              'members': sorted(members)}, indent=2) + '\n'
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix='.target-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            return True
        except OSError as exc:
            self._log(f'[target] selection not saved: {exc}')
            return False

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            self._log(f'[target] stored selection not removed: {exc}')
            return False
