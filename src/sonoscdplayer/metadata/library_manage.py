"""WEB-04b: disc-qualified edits and removal of *stored* metadata only.

All SQLite operations run on MetadataWorker's existing executor. An opaque
browser token is resolved against existing discs and manual_metadata rows;
raw disc IDs and TOCs never cross the player/HTTP boundary. Cover files are
independent and deliberately left untouched (cover management is separate).
"""
from __future__ import annotations

import copy
import json

from sonoscdplayer.metadata.cache import DiscCache
from sonoscdplayer.metadata.library_index import MAX_LIBRARY_ROWS, valid_token, _metadata
from sonoscdplayer.metadata.manual_edits import _valid_stored, disc_token, validate_edit
from sonoscdplayer.metadata.musicbrainz import DiscIdentity

MAX_EDIT_FIELDS = 4


def validate_manage(token, action, changes=None):
    """Validate a bounded, single-transaction offline library operation."""
    if not valid_token(token):
        raise ValueError('invalid library token')
    if action not in ('edit', 'reset', 'clear-manual', 'delete') or type(action) is not str:
        raise ValueError('invalid library action')
    if action != 'edit':
        if changes is not None:
            raise ValueError('unexpected edit fields')
        return token, action, ()
    if type(changes) is not list or not 1 <= len(changes) <= MAX_EDIT_FIELDS:
        raise ValueError('invalid number of field edits')
    fields, seen = [], set()
    for change in changes:
        if type(change) is not dict or not {'field', 'value'} <= set(change) or set(change) - {'field', 'track', 'value'}:
            raise ValueError('invalid edit item')
        field, track, value = validate_edit(change['field'], change.get('track'), change['value'])
        if (field, track) in seen:
            raise ValueError('duplicate edit field')
        seen.add((field, track))
        fields.append((field, track, value))
    return token, action, tuple(fields)


class StoredLibraryManager:
    """Transactional edits to existing cache rows, never CD or Sonos I/O."""

    def __init__(self, cache_path):
        self.cache = DiscCache(cache_path)

    def _identity(self, token):
        db = self.cache._connect()  # Read-only. Never create records on bad input.
        if db is None:
            raise ValueError('library entry not found')
        try:
            # Manual-only discs need the same lookup as normal cache entries.
            has_manual = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_metadata'").fetchone())
            sql = ('SELECT disc_id,toc FROM discs UNION SELECT disc_id,toc FROM manual_metadata'
                   if has_manual else 'SELECT disc_id,toc FROM discs')
            for n, (disc_id, toc) in enumerate(db.execute(sql)):
                if n >= MAX_LIBRARY_ROWS:
                    raise ValueError('library row limit exceeded')
                identity = DiscIdentity(disc_id, toc)
                if disc_token(identity) == token:
                    return identity
        finally:
            db.close()
        raise ValueError('library entry not found')

    @staticmethod
    def _manual_table(db):
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_metadata'").fetchone())

    def apply(self, *, token, action, changes=()):
        """Resolve a live stored identity and mutate only its exact database key."""
        identity = self._identity(token)
        db = self.cache._connect(write=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT local_json FROM discs WHERE disc_id=? AND toc=?',
                             (identity.disc_id, identity.toc)).fetchone()
            has_manual = self._manual_table(db)
            manual_row = (db.execute('SELECT fields_json FROM manual_metadata WHERE disc_id=? AND toc=?',
                                     (identity.disc_id, identity.toc)).fetchone()
                          if has_manual else None)
            if row is None and manual_row is None:
                raise ValueError('library entry no longer exists')
            if action == 'edit':
                # A track edit must refer to a real cached track, or an existing
                # manual-only track. Never create fictitious tracks for old CDs.
                count = len(_metadata(row[0])['tracks']) if row else None
                old = json.loads(manual_row[0]) if manual_row else {}
                if not _valid_stored(old):
                    raise ValueError('invalid stored manual edits')
                for field, track, value in changes:
                    if track is not None and not (track <= count if count is not None else
                                                  any(key.startswith(str(track) + ':') for key in old)):
                        raise ValueError('track not in stored CD')
                    key = field if track is None else f'{track}:{field}'
                    if value is None:
                        old.pop(key, None)
                    else:
                        old[key] = value
                if old:
                    db.execute('''CREATE TABLE IF NOT EXISTS manual_metadata (
                        disc_id TEXT NOT NULL, toc TEXT NOT NULL,
                        fields_json TEXT NOT NULL, PRIMARY KEY (disc_id,toc))''')
                    db.execute('''INSERT INTO manual_metadata(disc_id,toc,fields_json) VALUES (?,?,?)
                        ON CONFLICT(disc_id,toc) DO UPDATE SET fields_json=excluded.fields_json''',
                        (identity.disc_id, identity.toc, json.dumps(old, ensure_ascii=False)))
                elif manual_row is not None:
                    db.execute('DELETE FROM manual_metadata WHERE disc_id=? AND toc=?',
                               (identity.disc_id, identity.toc))
            elif action == 'reset':
                # Forget automatic/confirmed release cache, but keep protected
                # text corrections. A fresh lookup occurs at next insertion.
                db.execute('DELETE FROM discs WHERE disc_id=? AND toc=?',
                           (identity.disc_id, identity.toc))
            elif action == 'clear-manual':
                # Remove only protected text corrections, not cached releases.
                if has_manual:
                    db.execute('DELETE FROM manual_metadata WHERE disc_id=? AND toc=?',
                               (identity.disc_id, identity.toc))
            elif action == 'delete':
                # Delete only metadata/overrides for this CD. Release-shared and
                # manual cover files remain; cover cleanup is a separate action.
                db.execute('DELETE FROM discs WHERE disc_id=? AND toc=?',
                           (identity.disc_id, identity.toc))
                if has_manual:
                    db.execute('DELETE FROM manual_metadata WHERE disc_id=? AND toc=?',
                               (identity.disc_id, identity.toc))
            else:
                raise ValueError('invalid library action')
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return {'token': token, 'action': action}


class LibraryManageWorkflow:
    """Nonblocking mailbox for one active library write, with result polling."""

    def _init_library_manage(self, cache_path):
        self._library_manager = StoredLibraryManager(cache_path)
        self._library_manage_job = None
        self._library_manage_state = {'status': 'idle', 'seq': 0, 'token': None,
                                      'action': None, 'error': None}

    def enqueue_library_manage(self, *, token, action, changes=None):
        token, action, changes = validate_manage(token, action, changes)
        if self._library_manage_job is not None:
            raise RuntimeError('library edit still pending')
        # WEB-03 is the sole editor for the inserted disc. An offline edit
        # cannot race its current metadata snapshot or ongoing playback.
        if self._last_toc is not None:
            try:
                current = disc_token(self._identify(self._last_toc))
            except Exception as exc:
                raise ValueError('current disc identity unavailable') from exc
            if current == token:
                raise ValueError('inserted disc must use the current metadata editor')
        self._library_manage_state.update(status='saving', token=token,
                                          action=action, error=None)
        self._library_manage_state['seq'] += 1
        self._library_manage_job = self._executor.submit(self._library_manager.apply,
                                                          token=token, action=action,
                                                          changes=changes)

    def _poll_library_manage(self):
        future = self._library_manage_job
        if future is None or not future.done():
            return False
        self._library_manage_job = None
        try:
            result = future.result()
        except Exception:
            # Never expose SQLite paths or internal identities to the browser.
            self._library_manage_state.update(status='error',
                error='metadata.error.library_edit_failed')
        else:
            self._library_manage_state.update(status='ready', error=None)
            # A disc may have been inserted while the queued change was saved.
            # Re-read in that case; never leave a stale on-screen snapshot.
            toc = self._last_toc
            if toc is not None:
                try:
                    same_disc = disc_token(self._identify(toc)) == result['token']
                except Exception:
                    same_disc = False
                if same_disc:
                    self._result = None
                    self._pending = self._pending_toc = None
                    self._submit(toc)
        self._library_manage_state['seq'] += 1
        return True

    def library_snapshot(self):
        answer = super().library_snapshot()
        answer['library']['manage'] = copy.deepcopy(self._library_manage_state)
        return answer
