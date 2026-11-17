"""Read-only CD-TEXT probe and normalized local disc metadata.

This module never contacts Sonos or the internet. Call the optical probe only
while the player is idle and holding the existing drive_access.drive_lock.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import subprocess

from cd_toc import DiscTOC

_DISC = re.compile(r'^\s*CD-TEXT for Disc:\s*$', re.IGNORECASE)
_TRACK = re.compile(r'^\s*CD-TEXT for Track\s+(\d+):\s*$', re.IGNORECASE)
_FIELD = re.compile(r'^\s*([A-Z][A-Z0-9_ ]*):\s*(.*?)\s*$', re.IGNORECASE)

# cd-info reports ISRC and MCN above the CD-TEXT analysis section.
_TECH_ISRC = re.compile(r'^TRACK\s+(\d+)\s+ISRC:\s*([A-Z0-9]{12})\s*$', re.I)
_TECH_MCN = re.compile(r'^Media Catalog Number \(MCN\):\s*(\d{13})\s*$', re.I)


@dataclass(frozen=True)
class LocalTrackMetadata:
    number: int
    duration_seconds: float
    title: str | None = None
    artist: str | None = None
    isrc: str | None = None


@dataclass(frozen=True)
class LocalDiscMetadata:
    album: str | None
    artist: str | None
    tracks: tuple[LocalTrackMetadata, ...]
    cdtext_status: str  # available | absent | unavailable | failed | disabled
    cdtext_error: str | None = None
    mcn: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_cd_info_cdtext(output: str) -> tuple[dict[str, str], dict[int, dict[str, str]]]:
    """Only parse explicitly labelled CD-TEXT blocks, never CDDB/online data."""
    disc: dict[str, str] = {}
    tracks: dict[int, dict[str, str]] = {}
    section: dict[str, str] | None = None
    for line in output.splitlines():
        if _DISC.fullmatch(line):
            section = disc
            continue
        track_match = _TRACK.fullmatch(line)
        if track_match:
            number = int(track_match.group(1))
            section = tracks.setdefault(number, {}) if number > 0 else None
            continue
        if not line.strip():
            continue
        # Only indented lines may belong to a CD-TEXT block. Once a new
        # non-indented section appears, stop consuming arbitrary report fields.
        if not line[0].isspace():
            section = None
            continue
        field = _FIELD.fullmatch(line)
        if section is not None and field:
            key, value = field.group(1).upper().replace(' ', '_'), field.group(2).strip()
            if key in ('TITLE', 'PERFORMER', 'ISRC') and value:
                section[key] = value
    return disc, tracks



def parse_cd_info_identifiers(output: str) -> tuple[str | None, dict[int, str]]:
    """Extract technical MCN/ISRC without treating CD-TEXT as identifiers.

    All-zero placeholders and conflicting repeated identifiers are ignored.
    """
    mcn = None
    mcn_conflict = False
    isrcs: dict[int, str] = {}
    conflicted: set[int] = set()
    for line in output.split('CD Analysis Report', 1)[0].splitlines():
        match = _TECH_ISRC.fullmatch(line)
        if match:
            number, value = int(match.group(1)), match.group(2).upper()
            if number < 1 or value == '0' * 12 or number in conflicted:
                continue
            previous = isrcs.get(number)
            if previous is not None and previous != value:
                isrcs.pop(number, None)
                conflicted.add(number)
            else:
                isrcs[number] = value
            continue
        match = _TECH_MCN.fullmatch(line)
        if match and not mcn_conflict:
            value = match.group(1)
            if value == '0' * 13:
                continue
            if mcn is not None and mcn != value:
                mcn = None
                mcn_conflict = True
            else:
                mcn = value
    return mcn, isrcs


def read_cdtext(device: str, *, timeout: int = 15,
                runner=subprocess.run) -> tuple[str, dict, dict, str | None]:
    """Return (status, disc fields, track fields, sanitized error).

    cd-info does not replace the validated cdparanoia TOC. Its output is kept
    in memory and is not logged (it can contain personal CD titles).
    """
    try:
        result = runner(['cd-info', '--no-device-info', f'--input={device}'],
                        capture_output=True, text=True, timeout=timeout,
                        check=False)
    except FileNotFoundError:
        return 'unavailable', {}, {}, 'cd-info is not installed (libcdio-utils)'
    except subprocess.TimeoutExpired:
        return 'failed', {}, {}, 'cd-info timed out'
    except OSError as exc:
        return 'failed', {}, {}, type(exc).__name__
    if result.returncode != 0:
        return 'failed', {}, {}, f'cd-info exited with code {result.returncode}'
    disc, tracks = parse_cd_info_cdtext(result.stdout)
    has_cdtext = bool(disc or any(tracks.values()))
    mcn, isrcs = parse_cd_info_identifiers(result.stdout)
    if mcn is not None:
        disc.setdefault('MCN', mcn)
    for number, isrc in isrcs.items():
        tracks.setdefault(number, {}).setdefault('ISRC', isrc)
    # ISRC/MCN may be present even without any CD-TEXT.
    return ('available' if has_cdtext else 'absent'), disc, tracks, None


def build_local_metadata(toc: DiscTOC, *, cdtext_status: str,
                         disc_fields: dict | None = None,
                         track_fields: dict[int, dict] | None = None,
                         error: str | None = None) -> LocalDiscMetadata:
    """Use only validated TOC tracks; never trust CD-TEXT track numbering."""
    if cdtext_status not in ('available', 'absent', 'unavailable', 'failed', 'disabled'):
        raise ValueError('invalid CD-TEXT status')
    disc_fields = disc_fields or {}
    track_fields = track_fields or {}
    tracks = tuple(LocalTrackMetadata(
        number=track.number,
        duration_seconds=track.duration_seconds,
        title=track_fields.get(track.number, {}).get('TITLE'),
        artist=track_fields.get(track.number, {}).get('PERFORMER'),
        isrc=track_fields.get(track.number, {}).get('ISRC'),
    ) for track in toc.tracks)
    return LocalDiscMetadata(album=disc_fields.get('TITLE'),
                             artist=disc_fields.get('PERFORMER'),
                             tracks=tracks, cdtext_status=cdtext_status,
                             cdtext_error=error, mcn=disc_fields.get('MCN'))


#: (status, disc fields, track fields, error) as returned by read_cdtext().
NO_CDTEXT = ('disabled', {}, {}, None)


def local_metadata_from_cdtext(toc, cdtext=None) -> LocalDiscMetadata:
    """Combine the verified TOC with a CD-TEXT reading taken by the player."""
    status, disc_fields, track_fields, error = cdtext or NO_CDTEXT
    return build_local_metadata(toc, cdtext_status=status, disc_fields=disc_fields,
                                track_fields=track_fields, error=error)
