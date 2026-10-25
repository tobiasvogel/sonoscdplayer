#!/usr/bin/env python3
"""strict, read-only CD-DA TOC parsing using cdparanoia -Q.

No streaming, Sonos control or disk audio caching. Track ends are exclusive.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import subprocess

SECTOR_BYTES = 2352
SECTORS_PER_SECOND = 75
PCM_BYTES_PER_SECOND = 176400

ROW = re.compile(
    r"^\s*(?P<number>\d+)\.\s+"
    r"(?P<length>\d+)\s+\[[^\]]+\]\s+"
    r"(?P<start>\d+)\s+\[[^\]]+\]\s+"
    r"(?P<copy>yes|no)\s+(?P<pre>yes|no)\s+(?P<channels>\d+)\s*$",
    re.I,
)
TOTAL = re.compile(r"^\s*TOTAL\s+(?P<total>\d+)\s+\[[^\]]+\]", re.I)


class TOCError(ValueError):
    """Unusable or inconsistent CD-DA TOC."""


@dataclass(frozen=True)
class Track:
    number: int
    start_lsn: int
    end_lsn_exclusive: int
    channels: int
    preemphasis: bool

    @property
    def sectors(self) -> int:
        return self.end_lsn_exclusive - self.start_lsn

    @property
    def pcm_bytes(self) -> int:
        return self.sectors * SECTOR_BYTES

    @property
    def duration_seconds(self) -> float:
        return self.sectors / SECTORS_PER_SECOND


@dataclass(frozen=True)
class DiscTOC:
    tracks: tuple[Track, ...]
    leadout_lsn: int

    def get_track(self, number: int) -> Track:
        for track in self.tracks:
            if track.number == number:
                return track
        raise TOCError(f"Track {number} not found")


def parse_cdparanoia_query(output: str) -> DiscTOC:
    """Parse cdparanoia -Q text. Reject incomplete or inconsistent track maps."""
    parsed = []
    total_values = []
    for line in output.splitlines():
        match = ROW.match(line)
        if match:
            parsed.append((int(match['number']), int(match['start']),
                           int(match['length']), int(match['channels']),
                           match['pre'].lower() == 'yes'))
        match = TOTAL.match(line)
        if match:
            total_values.append(int(match['total']))

    if not parsed:
        raise TOCError('No CD-DA tracks found in cdparanoia TOC output')
    if len(total_values) != 1:
        raise TOCError('Exactly one TOTAL / lead-out entry required')
    if parsed[0][0] != 1 or parsed[0][1] != 0:
        raise TOCError('Expected first audio track number 1 at LSN 0')
    tracks = []
    for i, (num, start, length, channels, pre) in enumerate(parsed):
        if num != i + 1:
            raise TOCError(f'Nonconsecutive track number: {num}')
        if length <= 0:
            raise TOCError(f'Track {num} has invalid length {length}')
        if channels not in (2, 4):
            raise TOCError(f'Track {num} has unexpected channels={channels}')
        end = start + length
        if i and start != tracks[-1].end_lsn_exclusive:
            raise TOCError(f'Gap or overlap before track {num}')
        tracks.append(Track(num, start, end, channels, pre))
    leadout = total_values[0]
    if tracks[-1].end_lsn_exclusive != leadout:
        raise TOCError(f'Lead-out mismatch: last end={tracks[-1].end_lsn_exclusive}, TOTAL={leadout}')
    return DiscTOC(tuple(tracks), leadout)


def read_toc(device: str = '/dev/sr0', timeout: int = 30) -> DiscTOC:
    """Query the drive without ripping audio; no fallback to stale TOC."""
    try:
        result = subprocess.run(
            ['cdparanoia', '-d', device, '-Q'],
            text=True, capture_output=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise TOCError('cdparanoia is not installed') from exc
    except subprocess.TimeoutExpired as exc:
        raise TOCError(f'cdparanoia TOC query timed out after {timeout}s') from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-300:]
        raise TOCError(f'cdparanoia -Q failed (exit={result.returncode}): {detail}')
    return parse_cdparanoia_query(result.stdout + '\n' + result.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True, help='Current optical drive path (e.g. /dev/sr1)')
    args = parser.parse_args()
    try:
        toc = read_toc(args.device)
    except TOCError as exc:
        print(f'ERROR: {exc}')
        return 1
    print(f'DEVICE={args.device} TRACKS={len(toc.tracks)} LEADOUT_LSN={toc.leadout_lsn}')
    print('TRACK  START_LSN  END_EXCLUSIVE  SECTORS  DURATION_S  PCM_BYTES')
    for track in toc.tracks:
        print(f'{track.number:5d}  {track.start_lsn:9d}  {track.end_lsn_exclusive:13d}  '
              f'{track.sectors:7d}  {track.duration_seconds:10.3f}  {track.pcm_bytes:9d}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
