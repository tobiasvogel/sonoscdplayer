"""WEB-04c: bounded library maintenance, independent from playback or drive I/O.

The player thread only validates and schedules. SQLite and file stat/unlink
operations run on the existing metadata executor. No automatic cleanup of
manual covers, nor removal of a shared cover still referenced by a cache row.
"""
from __future__ import annotations

import copy
from hashlib import sha256
import os
from pathlib import Path
import re
import stat

from sonoscdplayer.metadata.cache import DiscCache
from sonoscdplayer.metadata.cover_lock import locked_cover_directory
from sonoscdplayer.metadata.cover_preferences import read_cover_preferences
from sonoscdplayer.metadata.library_index import MAX_LIBRARY_ROWS, valid_token
from sonoscdplayer.metadata.manual_edits import disc_token
from sonoscdplayer.metadata.musicbrainz import DiscIdentity

MAX_BULK = 40
_AUTO_NAME = re.compile(r'^[0-9a-f]{64}\.jpg$')
_MANUAL_NAME = re.compile(r'^manual-[0-9a-f]{32}\.jpg$')
_RELEASE_ID = re.compile(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
CONFIRM_ALL = 'DELETE ALL METADATA'
CONFIRM_COVERS = 'DELETE UNUSED AUTO COVERS'


def validate_maintenance(action, tokens=None, confirmation=None):
    """Strict wire contract, also enforced by the player (not just the UI)."""
    if type(action) is not str or action not in ('stats', 'bulk-delete', 'clear-all', 'prune-auto'):
        raise ValueError('invalid maintenance action')
    if action == 'bulk-delete':
        if (type(tokens) is not list or not 1 <= len(tokens) <= MAX_BULK or
                any(not valid_token(token) for token in tokens) or
                len(set(tokens)) != len(tokens) or confirmation is not None):
            raise ValueError('invalid selected CDs')
        return action, tuple(tokens), None
    if tokens is not None:
        raise ValueError('unexpected selected CDs')
    needed = {'clear-all': CONFIRM_ALL, 'prune-auto': CONFIRM_COVERS}.get(action)
    if (confirmation is not None if needed is None else confirmation != needed):
        raise ValueError('explicit maintenance confirmation required')
    return action, (), confirmation


class LibraryMaintenance:
    """Perform bounded SQLite operations and conservative cover housekeeping."""

    def __init__(self, cache_path, cover_dir, ui_path=None):
        self.cache = DiscCache(cache_path)
        self.cover_dir = Path(cover_dir)
        self.ui_path = ui_path

    @staticmethod
    def _manual_table(db):
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_metadata'").fetchone())

    def _keys(self, db):
        has_manual = self._manual_table(db)
        sql = ('SELECT disc_id,toc FROM discs UNION SELECT disc_id,toc FROM manual_metadata'
               if has_manual else 'SELECT disc_id,toc FROM discs')
        found = {}
        for index, (disc_id, toc) in enumerate(db.execute(sql)):
            if index >= MAX_LIBRARY_ROWS:
                raise ValueError('library row limit exceeded')
            token = disc_token(DiscIdentity(disc_id, toc))
            if token in found and found[token] != (disc_id, toc):
                raise ValueError('library token collision')
            found[token] = (disc_id, toc)
        return found, has_manual

    def _references(self, db):
        """Only genuine release IDs may protect auto covers; malformed rows fail closed."""
        references = set()
        for (release_id,) in db.execute('SELECT release_id FROM discs WHERE release_id IS NOT NULL'):
            if type(release_id) is not str or _RELEASE_ID.fullmatch(release_id) is None:
                raise ValueError('invalid release association; cover cleanup refused')
            references.add(sha256(release_id.lower().encode('ascii')).hexdigest() + '.jpg')
        return references

    def _files(self):
        try:
            entries = list(self.cover_dir.iterdir())
        except FileNotFoundError:
            return []
        result = []
        for path in entries:
            name = path.name
            if not (_AUTO_NAME.fullmatch(name) or _MANUAL_NAME.fullmatch(name)):
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                result.append((path, info.st_size, _MANUAL_NAME.fullmatch(name) is not None))
        return result

    def stats(self):
        db = self.cache._connect()
        total = 0
        references = set()
        if db is not None:
            try:
                total = len(self._keys(db)[0])
                references = self._references(db)
            finally:
                db.close()
        files = self._files()
        manual = [(path, size) for path, size, is_manual in files if is_manual]
        automatic = [(path, size) for path, size, is_manual in files if not is_manual]
        unused = [(path, size) for path, size in automatic if path.name not in references]
        try:
            db_size = self.cache.path.stat().st_size
        except FileNotFoundError:
            db_size = 0
        return {'disc_count': total, 'database_bytes': db_size,
                'auto_cover_count': len(automatic),
                'manual_cover_count': len(manual),
                'cover_bytes': sum(size for _, size, _ in files),
                'unused_auto_count': len(unused),
                'unused_auto_bytes': sum(size for _, size in unused),
                'budget_bytes': read_cover_preferences(self.ui_path).budget_mb * 1024 * 1024}

    def apply(self, *, action, tokens=(), confirmation=None, current=lambda: True):
        action, tokens, confirmation = validate_maintenance(
            action, list(tokens) if action == 'bulk-delete' else None, confirmation)
        if action == 'stats':
            return {'changed_count': 0, 'stats': self.stats()}
        if not current():
            raise ValueError('disc or playback state changed')
        if action == 'prune-auto':
            # Never prune manually uploaded covers or an unknown filename.
            db = self.cache._connect()
            if db is None:
                # Missing/corrupt library state cannot prove a cover is unused.
                raise ValueError('metadata database unavailable; cover cleanup refused')
            try:
                references = self._references(db)
            finally:
                db.close()
            # Capture identity before acquiring the directory descriptor. A
            # replacement between inventory and deletion must fail closed.
            candidates = []
            for path, _, manual in self._files():
                if manual or path.name in references:
                    continue
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    candidates.append((path.name, info.st_dev, info.st_ino))
            removed = 0
            if candidates:
                # Protect the stat/unlink pair from cooperating cover writers.
                # Keep identity checks: unrelated filesystem writers ignore flock.
                with locked_cover_directory(self.cover_dir) as fd:
                    for name, device, inode in candidates:
                        if not current():
                            raise ValueError('disc or playback state changed')
                        try:
                            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if (not stat.S_ISREG(info.st_mode) or
                                (info.st_dev, info.st_ino) != (device, inode)):
                            raise ValueError('cover changed during cleanup')
                        os.unlink(name, dir_fd=fd)
                        removed += 1
            return {'changed_count': removed, 'stats': self.stats()}
        if not self.cache.path.is_file():
            if action == 'bulk-delete':
                raise ValueError('library entry not found')
            return {'changed_count': 0, 'stats': self.stats()}
        db = self.cache._connect(write=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            found, has_manual = self._keys(db)
            if action == 'bulk-delete':
                missing = set(tokens) - found.keys()
                if missing:
                    raise ValueError('library entry no longer exists')
                targets = [found[token] for token in tokens]
            else:
                targets = list(found.values())
            if not current():
                raise ValueError('disc or playback state changed')
            if action == 'clear-all':
                # SQL transaction only; no VACUUM, cover unlink, drive or Sonos I/O.
                db.execute('DELETE FROM discs')
                if has_manual:
                    db.execute('DELETE FROM manual_metadata')
            else:
                for disc_id, toc in targets:
                    db.execute('DELETE FROM discs WHERE disc_id=? AND toc=?', (disc_id, toc))
                    if has_manual:
                        db.execute('DELETE FROM manual_metadata WHERE disc_id=? AND toc=?', (disc_id, toc))
            if not current():
                raise ValueError('disc or playback state changed')
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return {'changed_count': len(targets), 'stats': self.stats()}


class LibraryMaintenanceWorkflow:
    """One asynchronous maintenance operation; replies are cached in memory."""

    def _init_library_maintenance(self, cache_path, cover_dir, ui_path):
        self._maintenance = LibraryMaintenance(cache_path, cover_dir, ui_path)
        self._maintenance_job = None
        self._maintenance_state = {'status': 'idle', 'seq': 0, 'action': None,
                                   'changed_count': 0, 'stats': None, 'error': None}

    @property
    def destructive_maintenance_pending(self):
        """Keep playback gated until destructive maintenance is consumed."""
        return (self._maintenance_job is not None and
                self._maintenance_state['action'] != 'stats')

    def enqueue_library_maintenance(self, *, action, tokens=None, confirmation=None):
        action, tokens, confirmation = validate_maintenance(action, tokens, confirmation)
        if self._maintenance_job is not None:
            raise RuntimeError('maintenance already pending')
        if action != 'stats':
            if (self._library_manage_job is not None or self._edit_jobs or
                    self._cover_pending is not None or self._release_selection is not None or
                    getattr(self, '_reset_job', None) is not None):
                raise RuntimeError('metadata write pending')
            toc = self._last_toc
            if toc is not None:
                if action != 'bulk-delete':
                    raise ValueError('eject the CD before full cleanup')
                try:
                    active = disc_token(self._identify(toc))
                except Exception as exc:
                    raise ValueError('current disc identity unknown') from exc
                if active in tokens:
                    raise ValueError('cannot delete the inserted CD')
        expected_toc = self._last_toc
        def current():
            # Refuse queued destructive work if the CD has changed since approval.
            return (self._last_toc == expected_toc and
                    (action != 'bulk-delete' or self._last_toc is None or
                     disc_token(self._identify(self._last_toc)) not in tokens))
        self._maintenance_state.update(status='working', action=action,
                                       changed_count=0, error=None)
        self._maintenance_state['seq'] += 1
        self._maintenance_job = self._executor.submit(self._maintenance.apply,
            action=action, tokens=tokens, confirmation=confirmation,
            current=current)

    def _poll_library_maintenance(self):
        if self._maintenance_job is None or not self._maintenance_job.done():
            return False
        future = self._maintenance_job
        self._maintenance_job = None
        try:
            result = future.result()
        except Exception:
            self._maintenance_state.update(status='error',
                error='metadata.error.library_maintenance_failed')
        else:
            self._maintenance_state.update(status='ready', error=None,
                changed_count=result['changed_count'], stats=result['stats'])
        self._maintenance_state['seq'] += 1
        return True

    def library_snapshot(self):
        snapshot = super().library_snapshot()
        snapshot['library']['maintenance'] = copy.deepcopy(self._maintenance_state)
        return snapshot
