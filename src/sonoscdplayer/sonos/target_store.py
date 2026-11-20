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

__all__ = ['DEFAULT_STATE_DIR', 'PlaybackGroupStore', 'TargetStore', 'default_state_dir']

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


GROUP_FILE_NAME = 'playback_group.json'


class PlaybackGroupStore:
    """Remembers which speakers belong to the running playback.

    Written whenever the playback group changes (formed by the player, or
    changed in the Sonos app) and removed once it is released. After a
    restart or crash it tells the leftover cleanup which grouped speakers
    were ours, also those added in the Sonos app.
    """

    def __init__(self, directory=None, *, log=print):
        self.path = Path(directory if directory is not None else default_state_dir()) / GROUP_FILE_NAME
        self._log = log

    def load(self):
        """UIDs of the remembered playback group (empty if none). Never raises."""
        data = read_json(self.path, log=self._log)
        if data is None:
            return frozenset()
        uids = data.get('speakers') if isinstance(data, dict) and data.get('version') == VERSION else None
        if not isinstance(uids, list) or not all(isinstance(u, str) and UID_RE.fullmatch(u) for u in uids):
            self._log('[target] stored playback group ignored: invalid format')
            return frozenset()
        return frozenset(uids)

    def save(self, uids):
        """Remember the speakers of the playback group; an empty set forgets it."""
        if not uids:
            return self.clear()
        return write_json(self.path, {'version': VERSION, 'speakers': sorted(uids)}, log=self._log)

    def clear(self):
        """Forget the playback group."""
        if not self.path.exists():
            return True
        return remove(self.path, log=self._log)
