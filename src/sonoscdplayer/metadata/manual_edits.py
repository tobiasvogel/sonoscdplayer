"""Persistent, per-field manual metadata corrections for one validated disc.

Entries are keyed by MusicBrainz disc ID AND complete TOC, using a new table
in the existing SQLite database. This leaves the established v1 cache schema
and its migration guarantees intact. All I/O must run on the metadata worker,
never on the serialized player/control worker.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from sonoscdplayer.metadata.cache import DiscCache

FIELDS = frozenset(('album', 'artist', 'track_title', 'track_artist'))
MAX_TEXT = 160


def disc_token(identity):
    """Opaque, disc-and-TOC-qualified token used to reject stale browser edits."""
    return hashlib.sha256((identity.disc_id + '\0' + identity.toc).encode()).hexdigest()[:32]


def validate_edit(field, track, value):
    """Validate a single user edit; None means remove that manual override."""
    if type(field) is not str or field not in FIELDS:
        raise ValueError('invalid metadata field')
    if field.startswith('track_'):
        if type(track) is not int or not 1 <= track <= 99:
            raise ValueError('track number required')
    elif track is not None:
        raise ValueError('track is only accepted for track fields')
    if value is not None:
        if type(value) is not str or not value.strip() or len(value) > MAX_TEXT or any(
                not char.isprintable() for char in value):
            raise ValueError('invalid metadata text')
        value = value.strip()
    return field, track, value


def _key(field, track):
    return field if track is None else f'{track}:{field}'


def _valid_stored(data):
    if not isinstance(data, dict) or len(data) > 200:
        return False
    for key, value in data.items():
        if not isinstance(key, str):
            return False
        try:
            if ':' in key:
                n, field = key.split(':', 1)
                if not n.isascii() or not n.isdecimal() or str(int(n)) != n:
                    return False
                validate_edit(field, int(n), value)
            else:
                validate_edit(key, None, value)
        except (TypeError, ValueError):
            return False
        if value is None:
            return False
    return True


class ManualEdits:
    """Store protected field values in SQLite, alongside the existing cache."""

    def __init__(self, path):
        self.cache = DiscCache(path)

    def get(self, identity):
        db = self.cache._connect()
        if db is None:
            return {}
        try:
            row = db.execute('''SELECT name FROM sqlite_master WHERE type='table'
                                AND name='manual_metadata' ''').fetchone()
            if row is None:
                return {}
            found = db.execute('''SELECT fields_json FROM manual_metadata
                                  WHERE disc_id=? AND toc=?''',
                               (identity.disc_id, identity.toc)).fetchone()
        finally:
            db.close()
        if found is None:
            return {}
        try:
            value = json.loads(found[0])
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid manual metadata entry') from exc
        if not _valid_stored(value):
            raise ValueError('invalid manual metadata entry')
        return value

    def set(self, identity, *, field, track=None, value=None):
        field, track, value = validate_edit(field, track, value)
        db = self.cache._connect(write=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS manual_metadata (
                disc_id TEXT NOT NULL, toc TEXT NOT NULL,
                fields_json TEXT NOT NULL, PRIMARY KEY (disc_id,toc))''')
            row = db.execute('''SELECT fields_json FROM manual_metadata
                                WHERE disc_id=? AND toc=?''',
                             (identity.disc_id, identity.toc)).fetchone()
            fields = json.loads(row[0]) if row else {}
            if not _valid_stored(fields):
                raise ValueError('invalid manual metadata entry')
            key = _key(field, track)
            if value is None:
                fields.pop(key, None)
            else:
                fields[key] = value
            if fields:
                db.execute('''INSERT INTO manual_metadata (disc_id,toc,fields_json)
                    VALUES (?,?,?) ON CONFLICT(disc_id,toc)
                    DO UPDATE SET fields_json=excluded.fields_json''',
                    (identity.disc_id, identity.toc, json.dumps(fields, ensure_ascii=False)))
            else:
                db.execute('DELETE FROM manual_metadata WHERE disc_id=? AND toc=?',
                           (identity.disc_id, identity.toc))
            db.commit()
            return fields
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def delete(self, identity):
        """Reset-disc removes manual corrections too, without touching other CDs."""
        db = self.cache._connect()
        if db is None:
            return False
        try:
            if db.execute("SELECT name FROM sqlite_master WHERE name='manual_metadata'").fetchone() is None:
                return False
        finally:
            db.close()
        db = self.cache._connect(write=True)
        try:
            with db:
                result = db.execute('DELETE FROM manual_metadata WHERE disc_id=? AND toc=?',
                                    (identity.disc_id, identity.toc))
            return result.rowcount > 0
        finally:
            db.close()


def apply_edits(snapshot, edits):
    """Overlay stored corrections last, including over CD-TEXT and MusicBrainz.

    Makes fresh nested objects; raw cached/automatic metadata stays unchanged.
    """
    if not isinstance(snapshot, dict):
        return snapshot
    automatic = snapshot.get('_automatic')
    if not isinstance(automatic, dict):
        automatic = {key: value for key, value in snapshot.items()
                     if key not in ('manual_fields', '_automatic')}
    result = dict(automatic)
    result['_automatic'] = automatic
    rows = [dict(row) for row in automatic.get('tracks', ())]
    changed = []
    for key, value in edits.items():
        if not isinstance(value, str):
            continue
        if key in ('album', 'artist'):
            result[key] = value
            changed.append(key)
        elif ':' in key:
            index, field = key.split(':', 1)
            if field not in ('track_title', 'track_artist'):
                continue
            number = int(index)
            row = next((t for t in rows if t.get('number') == number), None)
            if row is not None:
                row['title' if field == 'track_title' else 'artist'] = value
                changed.append(key)
    result['tracks'] = rows
    result['edit_seq'] = snapshot.get('edit_seq', 0)
    result['manual_fields'] = sorted(changed)
    if changed:
        result['metadata_source'] = 'manual'
    return result
