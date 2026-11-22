"""Strict public projection of WEB-04a read-only CD library responses."""
from __future__ import annotations

from sonoscdplayer.metadata.library_index import MAX_PAGE_SIZE, clean_text, valid_token
from sonoscdplayer.metadata.release_selection import valid_release_id


def _entry(raw, *, detail=False):
    if type(raw) is not dict or not valid_token(raw.get('token')):
        return None
    count = raw.get('track_count')
    if count is not None and (type(count) is not int or not 1 <= count <= 99):
        return None
    updated = raw.get('updated_at')
    data = {'token': raw['token'], 'album': clean_text(raw.get('album')),
            'artist': clean_text(raw.get('artist')), 'track_count': count,
            'cached': raw.get('cached') is True,
            'updated_at': updated if type(updated) is int and 0 <= updated <= 4102444800 else None,
            'match_method': clean_text(raw.get('match_method'), 32),
            'release_id': raw.get('release_id') if valid_release_id(raw.get('release_id')) else None,
            'confirmed_by_user': raw.get('confirmed_by_user') is True,
            'manual_cover': raw.get('manual_cover') is True}
    if detail:
        tracks = raw.get('tracks')
        by_num = {}
        if isinstance(tracks, list):
            for track in tracks[:99]:
                if type(track) is dict and type(track.get('number')) is int:
                    n = track['number']
                    if 1 <= n <= (count or 99) and n not in by_num:
                        by_num[n] = {'number': n, 'title': clean_text(track.get('title')),
                                     'artist': clean_text(track.get('artist'))}
        data['tracks'] = ([by_num.get(n, {'number': n, 'title': None, 'artist': None})
                          for n in range(1, count + 1)] if count is not None else
                          [by_num[n] for n in sorted(by_num)])
        fields = raw.get('manual_fields')
        data['manual_fields'] = [field for field in fields[:200]
                                 if type(field) is str and len(field) <= 24] if isinstance(fields, list) else []
    return data


def _manage(raw):
    """Whitelist the write result, never cache identity internals or SQLite errors."""
    if type(raw) is not dict:
        raw = {}
    status = raw.get('status')
    if status not in ('idle', 'saving', 'ready', 'error'):
        status = 'idle'
    seq = raw.get('seq')
    action = raw.get('action')
    if action not in ('edit', 'reset', 'clear-manual', 'delete'):
        action = None
    token = raw.get('token') if valid_token(raw.get('token')) else None
    return {'status': status, 'seq': seq if type(seq) is int and 0 <= seq <= 1_000_000_000 else 0,
            'token': token, 'action': action,
            'error': clean_text(raw.get('error'), 100)}


def public_library(response):
    if type(response) is not dict or response.get('ok') is not True:
        raise ValueError('library unavailable')
    raw = response.get('library')
    if type(raw) is not dict:
        raise ValueError('missing library state')
    status = raw.get('status')
    if status not in ('idle', 'loading', 'ready', 'error'):
        raise ValueError('invalid library state')
    entries = raw.get('entries')
    detail = raw.get('detail')
    offset, limit, total, seq = (raw.get(key) for key in ('offset', 'limit', 'total', 'seq'))
    if type(offset) is not int or not 0 <= offset <= 100000:
        offset = 0
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        limit = 20
    if type(total) is not int or not 0 <= total <= 100000:
        total = 0
    if type(seq) is not int or not 0 <= seq <= 1_000_000_000:
        seq = 0
    return {'ok': True, 'library': {'status': status,
            'query': clean_text(raw.get('query'), 80) or '',
            'offset': offset, 'limit': limit, 'total': total, 'seq': seq,
            'entries': [item for item in (_entry(row) for row in (entries[:MAX_PAGE_SIZE]
                         if isinstance(entries, list) else [])) if item is not None],
            'detail': _entry(detail, detail=True) if detail is not None else None,
            'error': clean_text(raw.get('error'), 100),
            'manage': _manage(raw.get('manage'))}}
