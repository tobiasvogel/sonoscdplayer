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
    if not disc and not any(tracks.values()):
        return 'absent', {}, {}, None
    return 'available', disc, tracks, None


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
                             cdtext_error=error)
