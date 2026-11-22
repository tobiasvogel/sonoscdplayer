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
    }
