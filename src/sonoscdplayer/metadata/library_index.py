"""WEB-04a: read-only metadata library, never on the serialized player worker.

Entries come from the existing 'discs' cache and manual_metadata overrides.
For an override-only CD without a cached track list, unknown fields remain
unknown rather than being invented. Full TOCs, ISRCs, MCNs, paths and raw
database JSON are never returned to HTTP callers.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from threading import Lock

from sonoscdplayer.metadata.cache import DiscCache, DEFAULT_DB_PATH
from sonoscdplayer.metadata.cover_store import DEFAULT_COVER_DIR
from sonoscdplayer.metadata.manual_edits import disc_token
from sonoscdplayer.metadata.musicbrainz import DiscIdentity

PAGE_SIZE = 20
MAX_PAGE_SIZE = 40
MAX_QUERY = 80
MAX_OFFSET = 100000
MAX_LIBRARY_ROWS = 100000  # bounded work on a small SBC; report truncation


def clean_text(value, limit=160):
    if type(value) is not str:
        return None
    value = ''.join(c for c in value if c.isprintable()).strip()
    return value[:limit] or None


def valid_token(value):
    return (type(value) is str and len(value) == 32 and
            all(char in '0123456789abcdef' for char in value))


def validate_page(query='', offset=0, limit=PAGE_SIZE):
    if (type(query) is not str or len(query) > MAX_QUERY or
            any(not ch.isprintable() for ch in query)):
        raise ValueError('invalid library search')
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ValueError('invalid library offset')
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError('invalid library page size')
    return query.strip(), offset, limit


def _metadata(local_json):
    """Validate a cache row and whitelist its human-readable fields."""
    data = json.loads(local_json)
    if type(data) is not dict or type(data.get('tracks')) is not list:
        raise ValueError('invalid library cache entry')
    source = data['tracks']
    if not 1 <= len(source) <= 99:
        raise ValueError('invalid library track count')
    tracks = []
    for position, item in enumerate(source, 1):
        if type(item) is not dict or type(item.get('number')) is not int or item['number'] != position:
            raise ValueError('invalid library tracks')
        tracks.append({'number': position, 'title': clean_text(item.get('title')),
                       'artist': clean_text(item.get('artist'))})
    return {'album': clean_text(data.get('album')), 'artist': clean_text(data.get('artist')),
            'tracks': tracks}


class LibraryIndex:
    """Database access is read-only. Never triggers internet, drive or Sonos I/O."""

    def __init__(self, cache_path=DEFAULT_DB_PATH, cover_dir=DEFAULT_COVER_DIR):
        self.cache = DiscCache(cache_path)
        self.cover_dir = Path(cover_dir)

    def _entry(self, row, manual):
        disc_id, toc, raw, release_id, method, confirmed, updated = row
        identity = DiscIdentity(disc_id, toc)
        cached = raw is not None
        info = _metadata(raw) if cached else {'album': None, 'artist': None, 'tracks': []}
        token = disc_token(identity)
        edits = {}
        if manual is not None:
            value = manual.get((disc_id, toc))
            if value:
                try:
                    candidate = json.loads(value)
                    if type(candidate) is dict:
                        edits = {key: value for key, value in candidate.items()
                                 if type(key) is str and type(value) is str and
                                 clean_text(value) is not None}
                except (ValueError, TypeError):
                    pass
        for key in ('album', 'artist'):
            if key in edits:
                info[key] = clean_text(edits[key])
        tracks = info['tracks']
        manual_fields = []
        for key, value in edits.items():
            if key in ('album', 'artist'):
                manual_fields.append(key)
            elif ':' in key:
                n, field = key.split(':', 1)
                if (n.isascii() and n.isdecimal() and len(n) <= 2 and str(int(n)) == n and
                        field in ('track_title', 'track_artist') and 1 <= int(n) <= 99 and
                        (not cached or int(n) <= len(tracks))):
                    if not cached:
                        while len(tracks) < int(n):
                            tracks.append({'number': len(tracks) + 1, 'title': None, 'artist': None})
                    tracks[int(n) - 1]['title' if field == 'track_title' else 'artist'] = clean_text(value)
                    manual_fields.append(key)
        manually_covered = (self.cover_dir / ('manual-' + token + '.jpg')).is_file()
        return {'token': token, 'album': info['album'], 'artist': info['artist'],
                'track_count': len(tracks) if cached else None,
                'cached': cached,
                'updated_at': updated if type(updated) is int and updated >= 0 else None,
                'match_method': clean_text(method, 32) if cached else 'manual',
                'release_id': release_id if type(release_id) is str and len(release_id) == 36 else None,
                'confirmed_by_user': confirmed == 1,
                'manual_fields': sorted(manual_fields), 'manual_cover': manually_covered,
                'tracks': tracks}

    @staticmethod
    def _manual_map(db):
        # This table was introduced after the v1 discs cache. Absence is normal.
        found = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_metadata'").fetchone()
        if not found:
            return None
        return {(disc_id, toc): raw for disc_id, toc, raw in db.execute(
            'SELECT disc_id,toc,fields_json FROM manual_metadata')}

    def _entries(self):
        db = self.cache._connect()   # read-only SQLite URI and schema check
        if db is None:
            return
        try:
            manual = self._manual_map(db)
            cursor = db.execute('''SELECT disc_id,toc,local_json,release_id,match_method,
                                   confirmed_by_user,updated_at FROM discs
                                   ORDER BY updated_at DESC,disc_id,toc''')
            seen = set()
            count = 0
            for number, row in enumerate(cursor):
                if number >= MAX_LIBRARY_ROWS:
                    raise ValueError('library row limit exceeded')
                seen.add((row[0], row[1]))
                count += 1
                try:
                    yield self._entry(row, manual)
                except (ValueError, TypeError, KeyError, OSError, OverflowError):
                    # A damaged individual cache record never leaks raw DB data.
                    continue
            # Manual corrections can exist for a disc with no MusicBrainz match:
            # _safe_result only inserts 'discs' when it finds a release or GnuDB.
            for disc_id, toc in sorted(manual or {}):
                if (disc_id, toc) in seen:
                    continue
                count += 1
                if count > MAX_LIBRARY_ROWS:
                    raise ValueError('library row limit exceeded')
                try:
                    yield self._entry((disc_id, toc, None, None, None, 0, None), manual)
                except (ValueError, TypeError, KeyError, OSError, OverflowError):
                    continue
        finally:
            db.close()

    def page(self, *, query='', offset=0, limit=PAGE_SIZE):
        query, offset, limit = validate_page(query, offset, limit)
        wanted = query.casefold()
        total = 0
        entries = []
        for item in self._entries():
            # Search only meaningful user-visible metadata (album, artist, tracks).
            if wanted and not any(wanted in (value or '').casefold() for value in
                                  (item['album'], item['artist'],
                                   *(t['title'] for t in item['tracks']),
                                   *(t['artist'] for t in item['tracks']))):
                continue
            if offset <= total < offset + limit:
                entries.append({key: value for key, value in item.items() if key != 'tracks' and key != 'manual_fields'})
            total += 1
        return {'query': query, 'offset': offset, 'limit': limit,
                'total': total, 'entries': entries}

    def detail(self, token):
        if not valid_token(token):
            raise ValueError('invalid library token')
        for entry in self._entries():
            if entry['token'] == token:
                return entry
        raise ValueError('library entry not found')


class LibraryWorkflow:
    """Asynchronous mailbox: socket reads touch only bounded in-memory data."""

    def _init_library(self, cache_path, cover_dir):
        self._library_index = LibraryIndex(cache_path, cover_dir)
        self._library_gate = Lock()
        self._library_job = None
        self._library_state = {'status': 'idle', 'seq': 0, 'query': '', 'offset': 0,
                               'limit': PAGE_SIZE, 'total': 0, 'entries': [],
                               'detail': None, 'error': None}

    def enqueue_library(self, *, query='', offset=0, limit=PAGE_SIZE, token=None):
        if token is None:
            query, offset, limit = validate_page(query, offset, limit)
            method, kwargs = self._library_index.page, dict(query=query, offset=offset, limit=limit)
        else:
            if not valid_token(token):
                raise ValueError('invalid library token')
            method, kwargs = self._library_index.detail, dict(token=token)
        if self._library_job is not None:
            raise RuntimeError('library request pending')
        with self._library_gate:
            self._library_state['status'] = 'loading'
            self._library_state['error'] = None
            self._library_state['seq'] += 1
            if token is None:
                self._library_state.update(query=query, offset=offset, limit=limit,
                                           detail=None)
            else:
                self._library_state['detail'] = None
        self._library_job = (token is not None, self._executor.submit(method, **kwargs))

    def _poll_library(self):
        if self._library_job is None or not self._library_job[1].done():
            return False
        detail, future = self._library_job
        self._library_job = None
        try:
            result = future.result()
        except Exception:
            with self._library_gate:
                self._library_state.update(status='error', error='CD-Bibliothek nicht verfügbar')
                self._library_state['seq'] += 1
        else:
            with self._library_gate:
                if detail:
                    self._library_state['detail'] = result
                else:
                    self._library_state.update(result)
                self._library_state.update(status='ready', error=None)
                self._library_state['seq'] += 1
        return True

    def library_snapshot(self):
        with self._library_gate:
            return {'ok': True, 'library': copy.deepcopy(self._library_state)}
