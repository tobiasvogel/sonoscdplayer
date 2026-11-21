"""Standalone, read-only GnuDB/CDDB client. Not connected to the player yet.

M4c.1: calculate the legacy CDDB ID from an already verified DiscTOC;
query and read GnuDB records through an injectable HTTP opener. No drive,
cache, playback or Sonos access. Entries are *untrusted suggestions*; CDDB IDs
can collide and are not MusicBrainz release IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from sonoscdplayer.audio.table_of_contents import DiscTOC

ENDPOINT = 'https://gnudb.gnudb.org/~cddb/cddb.cgi'
USER_AGENT = 'SonosCDPlayer/0.1 (https://github.com/tobiasvogel/sonoscdplayer)'
HELLO = 'anonymous localhost SonosCDPlayer 0.1'
MAX_RESPONSE_BYTES = 256_000
MAX_MATCHES = 20
_ID = re.compile(r'[0-9a-fA-F]{8}\Z')
_CATEGORY = re.compile(r'[a-z0-9_-]{1,32}\Z')
_TRACK_TITLE = re.compile(r'TTITLE(0|[1-9][0-9]{0,2})\Z')


class CDDBError(ValueError):
    """Malformed, unverified or unsafe CDDB data."""


@dataclass(frozen=True)
class CDDBIdentity:
    disc_id: str
    offsets: tuple[int, ...]  # Absolute CDDB frames (LSN + 150).
    duration_seconds: int

    @property
    def query_command(self) -> str:
        return ('cddb query ' + self.disc_id + ' ' + str(len(self.offsets)) +
                ' ' + ' '.join(map(str, self.offsets)) + ' ' + str(self.duration_seconds))


def identity_from_toc(toc: DiscTOC) -> CDDBIdentity:
    """Classic freedb algorithm: digit sum of MSF seconds, modulo 255.

    cdparanoia start_lsn is 0-based; CDDB requires +150 frames. The
    leadout/first-track second offsets are truncated separately, as in CDDB.
    """
    if not isinstance(toc, DiscTOC) or not 1 <= len(toc.tracks) <= 99:
        raise CDDBError('invalid audio TOC')
    previous = None
    offsets = []
    checksum = 0
    for number, track in enumerate(toc.tracks, 1):
        if (track.number != number or type(track.start_lsn) is not int or
                type(track.end_lsn_exclusive) is not int or
                track.start_lsn < 0 or track.end_lsn_exclusive <= track.start_lsn or
                (previous is not None and track.start_lsn != previous)):
            raise CDDBError('invalid audio TOC track sequence')
        previous = track.end_lsn_exclusive
        offset = track.start_lsn + 150
        offsets.append(offset)
        checksum += sum(int(digit) for digit in str(offset // 75))
    if type(toc.leadout_lsn) is not int or toc.leadout_lsn != previous:
        raise CDDBError('invalid audio TOC lead-out')
    seconds = ((toc.leadout_lsn + 150) // 75) - (offsets[0] // 75)
    if not 0 < seconds <= 65535:
        raise CDDBError('disc duration outside CDDB range')
    disc_id = f'{(((checksum % 255) << 24) | (seconds << 8) | len(offsets)):08x}'
    return CDDBIdentity(disc_id, tuple(offsets), seconds)


@dataclass(frozen=True)
class CDDBMatch:
    category: str
    disc_id: str
    description: str


@dataclass(frozen=True)
class CDDBQueryResult:
    status: str  # unique | ambiguous | not_found | unavailable | offline | error
    matches: tuple[CDDBMatch, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class CDDBEntry:
    category: str
    disc_id: str
    album: str
    artist: str | None
    track_titles: tuple[str, ...]
    track_artists: tuple[str | None, ...]
    year: str | None = None


def _response_lines(raw: bytes) -> list[str]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CDDBError('CDDB response too large')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        # Protocol 6 normally returns UTF-8; older entries can be Latin-1.
        text = raw.decode('latin-1')
    lines = text.splitlines()
    if not lines or any(len(line) > 8192 for line in lines):
        raise CDDBError('invalid CDDB response')
    return lines


def _status_and_body(lines: list[str]) -> tuple[int, str, list[str]]:
    match = re.fullmatch(r'([0-9]{3})[ \t]+([^\r\n]*)', lines[0])
    if match is None:
        raise CDDBError('missing CDDB status')
    return int(match[1]), match[2], lines[1:]


def _parse_match(line: str) -> CDDBMatch:
    parts = line.split(None, 2)
    if (len(parts) != 3 or not _CATEGORY.fullmatch(parts[0]) or
            not _ID.fullmatch(parts[1]) or not parts[2].strip()):
        raise CDDBError('invalid CDDB match')
    return CDDBMatch(parts[0], parts[1].lower(), parts[2].strip())


def parse_query(raw: bytes) -> CDDBQueryResult:
    """Parse codes 200/210/211/202; reject truncation and unknown formats."""
    code, header, body = _status_and_body(_response_lines(raw))
    if code == 202:
        return CDDBQueryResult('not_found')
    if code == 200:
        return CDDBQueryResult('unique', (_parse_match(header),))
    if code in (210, 211):
        if '.' not in body:
            raise CDDBError('unterminated CDDB match list')
        end = body.index('.')
        if end > MAX_MATCHES or end == 0:
            raise CDDBError('invalid CDDB match count')
        matches = tuple(_parse_match(line) for line in body[:end])
        # Even a single returned inexact match is NOT an exact verified match.
        return CDDBQueryResult('ambiguous' if code == 211 or len(matches) > 1
                               else 'unique', matches)
    if code in (401, 402, 403, 409, 500, 501, 502, 503):
        return CDDBQueryResult('unavailable' if code in (402, 409, 503)
                               else 'error', error=f'CDDB status {code}')
    raise CDDBError('unsupported CDDB query response')


def _unescape(text: str) -> str:
    """Decode only documented xmcd escapes; never execute or interpret markup."""
    return re.sub(r'\\([nt\\])', lambda m: {'n': '\n', 't': '\t', '\\': '\\'}[m[1]], text)


def parse_entry(raw: bytes, *, identity: CDDBIdentity,
                match: CDDBMatch) -> CDDBEntry:
    """Accept xmcd only if its embedded TOC matches the inserted disc exactly."""
    code, _header, body = _status_and_body(_response_lines(raw))
    if code != 210:
        raise CDDBError(f'CDDB read status {code}')
    if '.' not in body:
        raise CDDBError('unterminated CDDB entry')
    body = body[:body.index('.')]
    if not body or not body[0].startswith('# xmcd'):
        raise CDDBError('missing xmcd signature')
    values: dict[str, str] = {}
    offsets = []
    collecting_offsets = False
    disc_length = None
    for line in body:
        if line.startswith('# Track frame offsets:'):
            collecting_offsets = True
            continue
        if collecting_offsets:
            frame = re.fullmatch(r'#\s+([0-9]+)\s*', line)
            if frame:
                offsets.append(int(frame[1]))
                continue
            collecting_offsets = False
        match_length = re.fullmatch(r'# Disc length:\s*([0-9]+)(?:\s+seconds)?\s*', line)
        if match_length:
            disc_length = int(match_length[1])
        if line.startswith('#') or not line:
            continue
        if '=' not in line:
            raise CDDBError('invalid xmcd field')
        key, value = line.split('=', 1)
        if key in ('DISCID', 'DTITLE', 'DYEAR') or _TRACK_TITLE.fullmatch(key):
            values[key] = values.get(key, '') + value
    if tuple(offsets) != identity.offsets or disc_length != identity.duration_seconds:
        raise CDDBError('CDDB entry TOC mismatch')
    ids = [item.strip().lower() for item in values.get('DISCID', '').split(',')]
    if match.disc_id not in ids or not all(_ID.fullmatch(item) for item in ids):
        raise CDDBError('CDDB entry ID mismatch')
    title = _unescape(values.get('DTITLE', '')).strip()
    if not title:
        raise CDDBError('missing CDDB album title')
    if ' / ' in title:
        artist, album = title.split(' / ', 1)
    else:
        artist, album = None, title
    if not album.strip():
        raise CDDBError('empty CDDB album')
    titles, artists = [], []
    for index in range(len(identity.offsets)):
        key = f'TTITLE{index}'
        if key not in values:
            raise CDDBError('incomplete CDDB track list')
        title = _unescape(values[key]).strip()
        if not title:
            raise CDDBError('empty CDDB track title')
        if ' / ' in title:
            track_artist, track_title = title.split(' / ', 1)
        else:
            track_artist, track_title = None, title
        titles.append(track_title.strip())
        artists.append(track_artist.strip() if track_artist else None)
    if any(_TRACK_TITLE.fullmatch(key) and int(key[6:]) >= len(titles)
           for key in values):
        raise CDDBError('extra CDDB tracks')
    year = _unescape(values.get('DYEAR', '')).strip() or None
    return CDDBEntry(match.category, match.disc_id, album.strip(),
                     artist.strip() if artist else None, tuple(titles),
                     tuple(artists), year)


def _get(command: str, *, opener, timeout_seconds: int, endpoint: str) -> bytes:
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
        raise ValueError('timeout_seconds must be 1..30')
    parsed = urllib.parse.urlsplit(endpoint)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise ValueError('CDDB endpoint must be an HTTPS CGI URL')
    query = urllib.parse.urlencode({'cmd': command, 'hello': HELLO, 'proto': '6'})
    request = urllib.request.Request(endpoint + '?' + query,
                                     headers={'User-Agent': USER_AGENT,
                                              'Accept': 'text/plain'})
    with opener(request, timeout=timeout_seconds) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CDDBError('CDDB response too large')
    return raw


def query_disc(identity: CDDBIdentity, *, timeout_seconds=5,
               opener=urllib.request.urlopen, endpoint=ENDPOINT) -> CDDBQueryResult:
    """One HTTPS request; errors are sanitized, never leak URLs or user data."""
    if not isinstance(identity, CDDBIdentity):
        raise ValueError('expected CDDBIdentity')
    try:
        return parse_query(_get(identity.query_command, opener=opener,
                                timeout_seconds=timeout_seconds, endpoint=endpoint))
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 502, 503, 504):
            return CDDBQueryResult('unavailable', error=f'GnuDB HTTP {exc.code}')
        return CDDBQueryResult('error', error=f'GnuDB HTTP {exc.code}')
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
        return CDDBQueryResult('offline', error='GnuDB unreachable')
    except (CDDBError, UnicodeError):
        return CDDBQueryResult('error', error='invalid GnuDB response')


def read_entry(identity: CDDBIdentity, match: CDDBMatch, *, timeout_seconds=5,
               opener=urllib.request.urlopen, endpoint=ENDPOINT) -> CDDBEntry:
    """Fetch a selected record; caller must still decide if names are trustworthy."""
    if (not isinstance(identity, CDDBIdentity) or not isinstance(match, CDDBMatch)
            or not _CATEGORY.fullmatch(match.category) or not _ID.fullmatch(match.disc_id)):
        raise ValueError('invalid CDDB entry request')
    command = f'cddb read {match.category} {match.disc_id}'
    return parse_entry(_get(command, opener=opener,
                            timeout_seconds=timeout_seconds, endpoint=endpoint),
                       identity=identity, match=match)
