"""Compact disc metadata for the player status (read by OLED, LED, buttons).

The status is polled several times a second by readers with small reply
limits (LED: 8 KB). It therefore carries only what a display needs for the
current track, never the whole track list; strings are bounded.
"""
from __future__ import annotations

MAX_TEXT = 160


def _text(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = ''.join(ch for ch in value if ch.isprintable()).strip()
    return value[:MAX_TEXT] or None


def track_entry(metadata, track):
    """The metadata track dict for ``track`` (1-based), or None."""
    for entry in (metadata or {}).get('tracks') or ():
        if isinstance(entry, dict) and entry.get('number') == track:
            return entry
    return None


def status_metadata(metadata, track):
    """Album, artists and the selected track's title for the status reply."""
    if not isinstance(metadata, dict):
        return None
    entry = track_entry(metadata, track) or {}
    cover = metadata.get('cover_file')
    return {
        'album': _text(metadata.get('album')),
        'artist': _text(metadata.get('artist')),
        'track_title': _text(entry.get('title')),
        'track_artist': _text(entry.get('artist')),
        'cover_file': cover if isinstance(cover, str) else None,
        'match_status': _text(metadata.get('match_status')),
        'cdtext_status': _text(metadata.get('cdtext_status')),
        'lookup_status': _text(metadata.get('lookup_status')),
        'cover_manual': metadata.get('cover_manual') is True,
        'cover_seq': metadata.get('cover_seq') if type(metadata.get('cover_seq')) is int else 0,
        'cover_error': _text(metadata.get('cover_error')),
    }
