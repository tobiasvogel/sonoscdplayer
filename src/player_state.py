"""Pure player state model (v0.11.0-alpha.1). No disc IO or Sonos side effects.

PLAYING means confirmed transport state, not a successful play_uri request.
Track selection while playing is rejected until an explicit safe stop/restart
workflow exists. Commands return a new immutable state or raise TransitionError.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from enum import Enum


class Mode(str, Enum):
    NO_DISC = 'NO_DISC'
    STOPPED = 'STOPPED'
    STARTING = 'STARTING'
    PLAYING = 'PLAYING'
    ERROR = 'ERROR'


class TransitionError(ValueError):
    pass


@dataclass(frozen=True)
class PlayerState:
    mode: Mode = Mode.NO_DISC
    track_numbers: tuple[int, ...] = ()
    selected_track: int | None = None
    error: str | None = None

    def __post_init__(self):
        if self.track_numbers and (tuple(sorted(set(self.track_numbers))) != self.track_numbers
                                   or any(n < 1 for n in self.track_numbers)):
            raise ValueError('track_numbers must be sorted positive unique numbers')
        if self.mode == Mode.NO_DISC and (self.track_numbers or self.selected_track is not None):
            raise ValueError('NO_DISC cannot have a disc or selection')
        if self.mode not in (Mode.NO_DISC, Mode.ERROR) and not self.track_numbers:
            raise ValueError('A ready player requires a disc')
        if self.selected_track is not None and self.selected_track not in self.track_numbers:
            raise ValueError('Selected track not on disc')

    def disc_loaded(self, numbers: tuple[int, ...]) -> 'PlayerState':
        if self.mode in (Mode.STARTING, Mode.PLAYING):
            raise TransitionError('Cannot change disc during active playback')
        if not numbers:
            raise TransitionError('Empty audio disc')
        return PlayerState(Mode.STOPPED, tuple(numbers), numbers[0])

    def disc_removed(self) -> 'PlayerState':
        if self.mode in (Mode.STARTING, Mode.PLAYING):
            raise TransitionError('Cannot remove disc logically while playing; handle transport first')
        return PlayerState()

    def select(self, track: int) -> 'PlayerState':
        self._require_stopped()
        if track not in self.track_numbers:
            raise TransitionError(f'Track {track} not present')
        return replace(self, selected_track=track)

    def next_track(self) -> 'PlayerState':
        self._require_stopped()
        index = self.track_numbers.index(self.selected_track)
        return replace(self, selected_track=self.track_numbers[min(index + 1, len(self.track_numbers)-1)])

    def previous_track(self) -> 'PlayerState':
        self._require_stopped()
        index = self.track_numbers.index(self.selected_track)
        return replace(self, selected_track=self.track_numbers[max(index - 1, 0)])

    def start_requested(self) -> 'PlayerState':
        self._require_stopped()
        return replace(self, mode=Mode.STARTING)

    def playback_confirmed(self) -> 'PlayerState':
        if self.mode != Mode.STARTING:
            raise TransitionError('Playback confirmation expected only after STARTING')
        return replace(self, mode=Mode.PLAYING)

    def natural_end(self) -> 'PlayerState':
        if self.mode not in (Mode.STARTING, Mode.PLAYING):
            raise TransitionError('No playback underway')
        return replace(self, mode=Mode.STOPPED)

    def failed(self, message: str) -> 'PlayerState':
        if not message.strip():
            raise ValueError('Error message must be nonempty')
        return replace(self, mode=Mode.ERROR, error=message)

    def reset_error(self) -> 'PlayerState':
        if self.mode != Mode.ERROR:
            raise TransitionError('Not in ERROR')
        if not self.track_numbers:
            return PlayerState()
        return replace(self, mode=Mode.STOPPED, error=None)

    def _require_stopped(self):
        if self.mode != Mode.STOPPED:
            raise TransitionError(f'Only permitted in STOPPED; current state {self.mode.value}')
