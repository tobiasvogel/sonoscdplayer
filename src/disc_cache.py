"""SQLite metadata cache; no audio or optical-drive access."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sqlite3
import time

from disc_metadata import LocalDiscMetadata, LocalTrackMetadata
from disc_musicbrainz import DiscIdentity

DEFAULT_DB_PATH = Path('/var/lib/sonoscdplayer/metadata.sqlite3')


@dataclass(frozen=True)
class CachedDisc:
    local: LocalDiscMetadata
    release_id: str | None
    match_method: str | None
    confidence_score: int | None
    confirmed_by_user: bool
    updated_at: int


class DiscCache:
    """Cache is keyed by BOTH DiscID and full TOC to avoid accidental collisions."""

    def __init__(self, path=DEFAULT_DB_PATH):
        self.path = Path(path)

    def _connect(self, *, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            previous_mask = os.umask(0o077)
            try:
                db = sqlite3.connect(self.path, timeout=3, isolation_level=None)
            finally:
                os.umask(previous_mask)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('''CREATE TABLE IF NOT EXISTS discs (
                disc_id TEXT NOT NULL, toc TEXT NOT NULL,
                local_json TEXT NOT NULL, release_id TEXT, match_method TEXT,
                confidence_score INTEGER, confirmed_by_user INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, PRIMARY KEY(disc_id,toc))''')
            db.execute('PRAGMA user_version=1')
            return db
        if not self.path.is_file():
            return None
        db = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True, timeout=3)
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            db.close()
            raise ValueError('unsupported metadata cache schema')
        return db

    def get(self, identity: DiscIdentity) -> CachedDisc | None:
        db = self._connect()
        if db is None:
            return None
        try:
            row = db.execute('''SELECT local_json,release_id,match_method,
                confidence_score,confirmed_by_user,updated_at FROM discs
                WHERE disc_id=? AND toc=?''',
                (identity.disc_id, identity.toc)).fetchone()
        finally:
            db.close()
        if row is None:
            return None
        try:
            data = json.loads(row[0])
            local = LocalDiscMetadata(
                data['album'], data['artist'],
                tuple(LocalTrackMetadata(**track) for track in data['tracks']),
                data['cdtext_status'], data.get('cdtext_error'), data.get('mcn'))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('invalid cached disc metadata') from exc
        return CachedDisc(local, row[1], row[2], row[3], bool(row[4]), row[5])

    def delete(self, identity: DiscIdentity) -> bool:
        """Remove this disc's entry, also a manually confirmed one."""
        if not self.path.is_file():
            return False
        db = self._connect(write=True)
        try:
            with db:
                cursor = db.execute('DELETE FROM discs WHERE disc_id=? AND toc=?',
                                    (identity.disc_id, identity.toc))
            return cursor.rowcount > 0
        finally:
            db.close()

    def put(self, identity: DiscIdentity, local: LocalDiscMetadata, *,
            release_id=None, match_method=None, confidence_score=None,
            confirmed_by_user=False) -> bool:
        """Never replace a manually confirmed release with another release."""
        if match_method not in (None, 'unique', 'confidence', 'manual'):
            raise ValueError('invalid match method')
        if release_id is None and (match_method is not None or confidence_score is not None
                                   or confirmed_by_user):
            raise ValueError('binding fields without release ID')
        if confirmed_by_user and match_method != 'manual':
            raise ValueError('manual confirmation must use manual method')
        if confidence_score is not None and (type(confidence_score) is not int
                                             or not 0 <= confidence_score <= 100):
            raise ValueError('invalid confidence score')
        if release_id is not None and (type(release_id) is not str or not release_id):
            raise ValueError('invalid release ID')
        data = json.dumps(asdict(local), ensure_ascii=False, separators=(',', ':'))
        db = self._connect(write=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('''SELECT release_id,match_method,confidence_score,
                confirmed_by_user FROM discs WHERE disc_id=? AND toc=?''',
                (identity.disc_id, identity.toc)).fetchone()
            if old and old[3] and release_id != old[0]:
                db.rollback()
                return False
            if old and old[3]:
                match_method, confirmed_by_user = 'manual', True
                confidence_score = old[2]
            db.execute('''INSERT INTO discs
                (disc_id,toc,local_json,release_id,match_method,confidence_score,
                 confirmed_by_user,updated_at) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(disc_id,toc) DO UPDATE SET
                local_json=excluded.local_json,
                release_id=excluded.release_id,
                match_method=excluded.match_method,
                confidence_score=excluded.confidence_score,
                confirmed_by_user=excluded.confirmed_by_user,
                updated_at=excluded.updated_at''',
                (identity.disc_id, identity.toc, data, release_id, match_method,
                 confidence_score, int(confirmed_by_user), int(time.time())))
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
