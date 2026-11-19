"""Read-only MusicBrainz identification and one-shot lookup.

No drive, Sonos or playback access. Online errors never replace local CD-TEXT.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from sonoscdplayer.audio.table_of_contents import DiscTOC

BASE_URL = 'https://musicbrainz.org/ws/2/discid/'
USER_AGENT = 'SonosCDPlayer/0.1 (https://github.com/tobiasvogel/sonoscdplayer)'
_DISC_ID = re.compile(r'^[A-Za-z0-9._-]{28}$')


class DiscIdentificationError(RuntimeError):
    """Invalid or unavailable MusicBrainz disc identity."""


@dataclass(frozen=True)
class DiscIdentity:
    disc_id: str
    toc: str


def identity_from_toc(toc: DiscTOC, *, libdiscid_module=None) -> DiscIdentity:
    """Use validated cdparanoia LSN, adjusted by 150 sectors; no drive read."""
    if not toc.tracks or len(toc.tracks) > 99 or any(
            track.number != i + 1 for i, track in enumerate(toc.tracks)):
        raise DiscIdentificationError('invalid audio TOC')
    if toc.leadout_lsn <= toc.tracks[-1].start_lsn:
        raise DiscIdentificationError('invalid TOC lead-out')
    if libdiscid_module is None:
        try:
            import libdiscid as libdiscid_module
        except ImportError as exc:
            raise DiscIdentificationError('python3-libdiscid unavailable to this interpreter') from exc
    offsets = [track.start_lsn + 150 for track in toc.tracks]
    try:
        disc = libdiscid_module.put(1, len(offsets), toc.leadout_lsn + 150, offsets)
        disc_id, toc_string = str(disc.id), str(disc.toc)
    except (ValueError, RuntimeError, OSError) as exc:
        raise DiscIdentificationError('libdiscid rejected TOC') from exc
    if not _DISC_ID.fullmatch(disc_id) or not toc_string:
        raise DiscIdentificationError('invalid libdiscid output')
    return DiscIdentity(disc_id, toc_string)


@dataclass(frozen=True)
class ReleaseCandidate:
    release_id: str
    title: str
    artist: str | None
    country: str | None
    date: str | None
    track_titles: tuple[str | None, ...]
    # Per-track artist credit (differs from ``artist`` on compilations).
    track_artists: tuple[str | None, ...] = ()


def artist_credit(value):
    if not isinstance(value, list):
        return None
    parts = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get('name'), str):
            parts.append(item['name'])
            if isinstance(item.get('joinphrase'), str):
                parts.append(item['joinphrase'])
    return ''.join(parts) or None


_NUMBERED_TITLE = re.compile(r'^\s*(\d{1,2})(?:[.)][ \t]*|[ \t]+-[ \t]+|[ \t]+)(\S.*)$')


def _without_redundant_track_numbers(titles):
    """Remove prefixes only when MusicBrainz titles form a numbered sequence.

    A single title beginning with a number may be the actual song title.
    Preserve incomplete or inconsistently numbered lists unchanged.
    """
    numbered = []
    for position, title in enumerate(titles, 1):
        if not title:
            continue
        match = _NUMBERED_TITLE.fullmatch(title)
        if match is None or int(match.group(1)) != position:
            return tuple(titles)
        numbered.append((position - 1, match.group(2)))
    if len(numbered) < 2:
        return tuple(titles)
    result = list(titles)
    for index, title in numbered:
        result[index] = title
    return tuple(result)


def parse_release_candidates(data, *, track_count):
    """Retain candidates; only attach titles when the medium is unambiguous."""
    if not isinstance(data, dict) or not isinstance(data.get('releases', []), list):
        raise ValueError('invalid MusicBrainz response')
    candidates, seen = [], set()
    for release in data.get('releases', [])[:100]:
        if not isinstance(release, dict):
            continue
        release_id, title = release.get('id'), release.get('title')
        if not isinstance(release_id, str) or not isinstance(title, str) or not title or release_id in seen:
            continue
        seen.add(release_id)
        matching_media = []
        media = release.get('media', [])
        for medium in media if isinstance(media, list) else []:
            if not isinstance(medium, dict) or medium.get('track-count') != track_count:
                continue
            tracks = medium.get('tracks')
            if not isinstance(tracks, list) or len(tracks) != track_count:
                continue
            titles, artists = [], []
            for track in tracks:
                if not isinstance(track, dict):
                    titles.append(None)
                    artists.append(None)
                    continue
                recording = track.get('recording')
                recording = recording if isinstance(recording, dict) else {}
                title_value = track.get('title') or recording.get('title')
                titles.append(title_value if isinstance(title_value, str) else None)
                artists.append(artist_credit(track.get('artist-credit'))
                               or artist_credit(recording.get('artist-credit')))
            matching_media.append((_without_redundant_track_numbers(titles),
                                   tuple(artists)))
        candidates.append(ReleaseCandidate(
            release_id, title, artist_credit(release.get('artist-credit')),
            release.get('country') if isinstance(release.get('country'), str) else None,
            release.get('date') if isinstance(release.get('date'), str) else None,
            matching_media[0][0] if len(matching_media) == 1 else (),
            matching_media[0][1] if len(matching_media) == 1 else ()))
    return tuple(candidates)


@dataclass(frozen=True)
class LookupResult:
    status: str  # unique | ambiguous | not_found | offline | unavailable | error
    releases: tuple[ReleaseCandidate, ...] = ()
    error: str | None = None

    @property
    def selected_release_id(self):
        return self.releases[0].release_id if self.status == 'unique' else None


def lookup_disc(identity: DiscIdentity, *, track_count: int, timeout_seconds=8,
                opener=urllib.request.urlopen) -> LookupResult:
    """One MusicBrainz GET; no retries, writes or exposure of network error text."""
    if not 1 <= track_count <= 99 or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 30:
        raise ValueError('invalid lookup parameters')
    if not _DISC_ID.fullmatch(identity.disc_id):
        raise ValueError('invalid disc id')
    query = urllib.parse.urlencode({
        'inc': 'recordings+artist-credits', 'toc': identity.toc,
        'cdstubs': 'no', 'fmt': 'json'})
    request = urllib.request.Request(
        BASE_URL + urllib.parse.quote(identity.disc_id, safe='') + '?' + query,
        headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    try:
        with opener(request, timeout=timeout_seconds) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            return LookupResult('error', error='MusicBrainz response too large')
        candidates = parse_release_candidates(json.loads(raw.decode('utf-8')),
                                              track_count=track_count)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return LookupResult('not_found')
        if exc.code in (429, 502, 503, 504):
            # Do not automatically retry a temporarily overloaded service.
            return LookupResult('unavailable', error=f'MusicBrainz temporarily unavailable (HTTP {exc.code})')
        return LookupResult('error', error=f'MusicBrainz HTTP {exc.code}')
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
        return LookupResult('offline', error='MusicBrainz unreachable')
    except (UnicodeError, ValueError):
        return LookupResult('error', error='invalid MusicBrainz response')
    if not candidates:
        return LookupResult('not_found')
    return LookupResult('unique' if len(candidates) == 1 else 'ambiguous', candidates)
