"""Track information and album art for Sonos (DIDL-Lite), from disc metadata.

Pure functions: no network, no Sonos calls. The player passes the result to
``play_uri(meta=...)``. Metadata that arrives during playback is used from
the next track on; a running track is never restarted for it (D-058).
"""
from __future__ import annotations

from pathlib import Path
import re
from xml.sax.saxutils import escape

from metadata_status import track_entry

DEFAULT_ALBUM_PREFIX = 'CD: '
MAX_PREFIX = 24
COVER_PREFIX = '/cover/'
_COVER_NAME = re.compile(r'^[0-9a-f]{64}\.jpg$')
MAX_FIELD = 200


def album_prefix_from_config(settings) -> str:
    """[sonos] album_prefix in player.toml; empty string switches it off."""
    sonos = settings.get('sonos', {}) if isinstance(settings, dict) else {}
    if not isinstance(sonos, dict):
        raise ValueError('[sonos] must be a table')
    value = sonos.get('album_prefix', DEFAULT_ALBUM_PREFIX)
    if not isinstance(value, str) or len(value) > MAX_PREFIX or \
            any(not ch.isprintable() for ch in value):
        raise ValueError(f'sonos.album_prefix must be printable text, at most {MAX_PREFIX} characters')
    return value


def cover_route(path) -> str | None:
    """URL path for a cached cover file (name = sha256 of the release id)."""
    if not isinstance(path, str) or not path:
        return None
    name = Path(path).name
    return COVER_PREFIX + name if _COVER_NAME.fullmatch(name) else None


def _field(value):
    if not isinstance(value, str):
        return None
    value = ''.join(ch for ch in value if ch.isprintable()).strip()
    return value[:MAX_FIELD] or None


def track_didl(url, metadata, track, *, album_prefix=DEFAULT_ALBUM_PREFIX, cover_url=None):
    """DIDL-Lite for one track, or None without metadata (plain play_uri then)."""
    if not isinstance(metadata, dict):
        return None
    entry = track_entry(metadata, track) or {}
    title = _field(entry.get('title')) or f'Track {track}'
    artist = _field(entry.get('artist')) or _field(metadata.get('artist'))
    album = _field(metadata.get('album'))
    if album is not None:
        album = (album_prefix or '') + album
    parts = [
        '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">',
        '<item id="-1" parentID="-1" restricted="true">',
        f'<dc:title>{escape(title)}</dc:title>',
    ]
    if artist:
        parts.append(f'<dc:creator>{escape(artist)}</dc:creator>')
        parts.append(f'<upnp:artist>{escape(artist)}</upnp:artist>')
    if album:
        parts.append(f'<upnp:album>{escape(album)}</upnp:album>')
    if cover_url:
        parts.append(f'<upnp:albumArtURI>{escape(cover_url)}</upnp:albumArtURI>')
    parts += ['<upnp:class>object.item.audioItem.musicTrack</upnp:class>',
              f'<res protocolInfo="http-get:*:audio/wav:*">{escape(url)}</res>',
              '</item></DIDL-Lite>']
    return ''.join(parts)
