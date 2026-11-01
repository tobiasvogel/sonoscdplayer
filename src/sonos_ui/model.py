"""Immutable display model, independent of storage, Sonos and GPIO."""
from dataclasses import dataclass
from typing import Literal

View = Literal['idle', 'playback', 'menu']

@dataclass(frozen=True)
class DisplayModel:
    view: View = 'idle'
    disc_present: bool = True
    album: str | None = None
    artist: str | None = None
    title: str | None = None
    track: int = 1
    track_count: int = 0
    cover: str | None = None
    playback: Literal['stopped', 'playing', 'paused'] = 'stopped'
    speaker: str | None = None
    elapsed_seconds: int | None = None
    duration_seconds: int | None = None
    speakers: tuple[str, ...] = ()
    speakers_searching: bool = False
    focus_index: int = 0
    selected_index: int | None = None
    disc_phase: str | None = None
