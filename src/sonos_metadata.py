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


def use_queue_from_config(settings) -> bool:
    """[sonos] use_queue (default true): play from the queue so the app shows artist/album."""
    sonos = settings.get('sonos', {}) if isinstance(settings, dict) else {}
    value = sonos.get('use_queue', True) if isinstance(sonos, dict) else True
    if type(value) is not bool:
        raise ValueError('sonos.use_queue must be true or false')
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


def _duration(seconds):
    if type(seconds) not in (int, float) or not 0 < seconds < 360000:
        return None
    total = int(seconds + 0.5)
    return f'{total // 3600}:{total // 60 % 60:02d}:{total % 60:02d}'


def track_didl(url, metadata, track, *, album_prefix=DEFAULT_ALBUM_PREFIX, cover_url=None,
               duration_seconds=None):
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
    # The track length lets the Sonos app show duration and progress.
    length = _duration(entry.get('duration_seconds')) or _duration(duration_seconds)
    duration = f' duration="{length}"' if length else ''
    parts += ['<upnp:class>object.item.audioItem.musicTrack</upnp:class>',
              f'<res protocolInfo="http-get:*:audio/wav:*"{duration}>{escape(url)}</res>',
              '</item></DIDL-Lite>']
    return ''.join(parts)


# ---- playing from the queue (the Sonos app shows artist and album only there) ----

def queue_allowed(player):
    """Only with plain play mode: shuffle/repeat would continue with the user's queue."""
    try:
        return player.play_mode == 'NORMAL'
    except Exception:
        return False


def didl_item(didl):
    from soco.data_structures_entry import from_didl_string
    return from_didl_string(didl)[0]


def queue_item_is_ours(player, position, url):
    """True only if queue position (1-based) still holds exactly our stream."""
    try:
        items = player.get_queue(start=position - 1, max_items=1)
        return bool(items) and getattr(items[0].resources[0], 'uri', None) == url
    except Exception:
        return False


def remove_queue_item(player, entry):
    """Remove our entry (position, url) if it is still ours; never anything else."""
    if entry is None:
        return False
    position, url = entry
    if not queue_item_is_ours(player, position, url):
        return False
    try:
        player.remove_from_queue(position - 1)
        return True
    except Exception:
        return False


def playing_from_queue(player):
    try:
        uri = player.avTransport.GetMediaInfo([('InstanceID', 0)])['CurrentURI']
    except Exception:
        return False
    return isinstance(uri, str) and uri.startswith('x-rincon-queue:')
