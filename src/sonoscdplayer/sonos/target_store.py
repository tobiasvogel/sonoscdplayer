"""Remember the selected Sonos speakers across restarts.

The selection is kept in ``target.json`` in the player's state directory
(see ``state_files``), never in the configuration files under ``/etc``.
Restoring a selection contacts no speaker and never starts playback; PLAY
performs the usual live checks. A missing, unreadable or invalid
file simply means "nothing selected".
"""
from __future__ import annotations

from pathlib import Path

from sonoscdplayer.sonos.groups import UID_RE, normalize_members
from sonoscdplayer.system.state_files import DEFAULT_STATE_DIR, default_state_dir, read_json, remove, write_json

__all__ = ['DEFAULT_STATE_DIR', 'TargetStore', 'default_state_dir']

FILE_NAME = 'target.json'
VERSION = 1


class TargetStore:
    """Reads and writes ``target.json`` in the state directory."""

    def __init__(self, directory=None, *, log=print):
        self.path = Path(directory if directory is not None else default_state_dir()) / FILE_NAME
        self._log = log

    def load(self):
        """Return (coordinator_uid, members) or None. Never raises."""
        data = read_json(self.path, log=self._log)
        if data is None:
            return None
        try:
            if not isinstance(data, dict) or data.get('version') != VERSION:
                raise ValueError('unknown format')
            uid = data.get('coordinator')
            if not isinstance(uid, str) or not UID_RE.fullmatch(uid):
                raise ValueError('invalid coordinator')
            members = tuple(sorted(normalize_members(uid, data.get('members', []))))
            return uid, members
        except ValueError as exc:
            self._log(f'[target] stored selection ignored: {exc}')
            return None

    def save(self, uid, members):
        """Remember coordinator and members; False if the file cannot be written."""
        return write_json(self.path, {'version': VERSION, 'coordinator': uid,
                                      'members': sorted(members)}, log=self._log)

    def clear(self):
        """Forget the remembered selection."""
        return remove(self.path, log=self._log)
