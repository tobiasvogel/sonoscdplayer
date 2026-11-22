"""Public, bounded read-only projection of the player's dashboard snapshot.

The player provides only track titles and room names here. Never forward
raw control replies or speaker UIDs to a LAN browser.
"""
from __future__ import annotations


def _text(value, limit=160):
    if type(value) is not str:
        return None
    cleaned = ''.join(ch for ch in value if ch.isprintable()).strip()
    return cleaned[:limit] or None


def _release_id(value):
    from sonoscdplayer.metadata.release_selection import valid_release_id
    return value if valid_release_id(value) else None


def public_release_candidates(candidates):
    """Whitelist only safe summary fields; never publish provider response objects."""
    if not isinstance(candidates, list):
        return []
    result = []
    for item in candidates[:100]:
        if not isinstance(item, dict) or not _release_id(item.get('release_id')):
            continue
        score = item.get('score')
        count = item.get('track_count')
        result.append({'release_id': item['release_id'],
                       'title': _text(item.get('title')),
                       'artist': _text(item.get('artist')),
                       'country': _text(item.get('country'), 40),
                       'date': _text(item.get('date'), 40),
                       'score': score if type(score) is int and 0 <= score <= 100 else None,
                       'track_count': count if type(count) is int and 0 <= count <= 99 else 0})
    return result


def public_release_detail(data):
    """One candidate's tracklist, with no filesystem or private provider fields."""
    if not isinstance(data, dict) or not _release_id(data.get('release_id')):
        raise ValueError('invalid release detail')
    tracks = []
    for entry in data.get('tracks', [])[:99]:
        if not isinstance(entry, dict) or type(entry.get('number')) is not int:
            continue
        if not 1 <= entry['number'] <= 99:
            continue
        tracks.append({'number': entry['number'], 'title': _text(entry.get('title')),
                       'artist': _text(entry.get('artist'))})
    return {'ok': True, 'release_id': data['release_id'], 'title': _text(data.get('title')),
            'artist': _text(data.get('artist')), 'country': _text(data.get('country'), 40),
            'date': _text(data.get('date'), 40), 'tracks': tracks,
            'can_keep_track_edits': data.get('can_keep_track_edits') is True,
            'track_count': (data.get('track_count') if type(data.get('track_count')) is int
                            and 0 <= data['track_count'] <= 99 else 0)}


def public_dashboard(response):
    """Expose only complete-disc track labels and visible room names."""
    if not isinstance(response, dict) or response.get('ok') is not True:
        raise ValueError('dashboard unavailable')
    payload = response.get('dashboard')
    if not isinstance(payload, dict):
        raise ValueError('missing dashboard')
    count = payload.get('track_count')
    count = count if type(count) is int and 0 <= count <= 99 else 0
    provided = payload.get('tracks')
    by_number = {}
    if isinstance(provided, list):
        for track in provided[:99]:
            if not isinstance(track, dict):
                continue
            number = track.get('number')
            if type(number) is int and 1 <= number <= count and number not in by_number:
                by_number[number] = {
                    'number': number,
                    'title': _text(track.get('title')),
                    'artist': _text(track.get('artist')),
                }
    tracks = [by_number.get(number, {'number': number, 'title': None, 'artist': None})
              for number in range(1, count + 1)]
    names = payload.get('speaker_names')
    names = ([_text(name, 80) for name in names[:16]] if isinstance(names, list) else [])
    # Device IDs are never user-facing labels, even if discovery returns one.
    names = [name for name in names if name and not name.startswith('RINCON_')]
    selected_count = payload.get('speaker_count')
    if type(selected_count) is not int or not 0 <= selected_count <= 16:
        selected_count = 0
    return {
        'ok': True,
        'track_count': count,
        'tracks': tracks,
        'speaker_names': names,
        'speaker_count': selected_count,
        'target_rev': payload.get('target_rev') if type(payload.get('target_rev')) is int else None,
        'playing_on_selection': payload.get('playing_on_selection') is True,
        'edit_token': (payload.get('edit_token') if isinstance(payload.get('edit_token'), str)
                       and len(payload['edit_token']) == 32 and
                       all(char in '0123456789abcdef' for char in payload['edit_token']) else None),
        'manual_fields': ([field for field in payload.get('manual_fields', [])[:200]
                           if isinstance(field, str) and len(field) <= 24]
                          if isinstance(payload.get('manual_fields'), list) else []),
        'metadata_source': _text(payload.get('metadata_source'), 40),
        'album': _text(payload.get('album')),
        'artist': _text(payload.get('artist')),
        'edit_error': _text(payload.get('edit_error'), 80),
        'edit_seq': (payload.get('edit_seq') if type(payload.get('edit_seq')) is int
                     and payload['edit_seq'] >= 0 else 0),
        'release_status': (payload.get('release_status') if payload.get('release_status') in
                           ('idle', 'searching', 'ready', 'not_found', 'error', 'saving') else 'idle'),
        'release_candidates': public_release_candidates(payload.get('release_candidates')),
        'release_total': (payload.get('release_total') if type(payload.get('release_total')) is int
                          and 0 <= payload['release_total'] <= 100 else 0),
        'release_error': _text(payload.get('release_error'), 100),
        'release_seq': (payload.get('release_seq') if type(payload.get('release_seq')) is int
                        and payload['release_seq'] >= 0 else 0),
        'release_selected_id': _release_id(payload.get('release_selected_id')),
    }
