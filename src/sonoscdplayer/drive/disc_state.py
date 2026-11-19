"""What is in the drive: no drive, no disc, an audio CD, an unreadable disc.

This is separate from the player's playback state (player/state.py). A disc
status is only an observation; it never starts playback or a drive command.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class DiscMode(str, Enum):
    """The kind of situation in the drive."""
    NO_DRIVE = 'NO_DRIVE'
    NO_DISC = 'NO_DISC'
    LOADING = 'LOADING'
    READY = 'READY'
    UNREADABLE = 'UNREADABLE'
    EJECTED = 'EJECTED'


@dataclass(frozen=True)
class DiscStatus:
    """Disc situation, the drive's device path and the number of audio tracks.

    Only READY carries a device and a track count; the constructor rejects
    inconsistent combinations.
    """
    mode: DiscMode
    device: str | None = None
    track_count: int = 0
    error: str | None = None

    def __post_init__(self):
        if self.device is not None and not re.fullmatch(r'/dev/sr[0-9]+', self.device):
            raise ValueError('invalid optical device')
        if type(self.track_count) is not int or self.track_count < 0:
            raise ValueError('invalid track count')
        if self.mode == DiscMode.READY:
            if self.device is None or self.track_count == 0 or self.error is not None:
                raise ValueError('READY requires device/tracks and no error')
        elif self.track_count != 0:
            raise ValueError('non-ready disc state cannot retain track data')
        if self.mode == DiscMode.NO_DRIVE and self.device is not None:
            raise ValueError('NO_DRIVE cannot name a device')

    @classmethod
    def ready(cls, device: str, track_count: int) -> 'DiscStatus':
        """An audio CD with ``track_count`` tracks in ``device``."""
        return cls(DiscMode.READY, device=device, track_count=track_count)

    def snapshot(self) -> dict:
        """The status as a plain dict for the status reply."""
        return {'mode': self.mode.value, 'device': self.device,
                'track_count': self.track_count, 'error': self.error}
