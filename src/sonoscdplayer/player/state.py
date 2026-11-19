"""The player's state: mode, tracks of the disc, selected track, error.

Immutable: every transition returns a new PlayerState or raises
TransitionError. PLAYING means Sonos confirmed playback, not merely that a
play request was sent. No disc or Sonos access here.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from enum import Enum


class Mode(str, Enum):
    """What the player is doing."""
    NO_DISC = 'NO_DISC'
    STOPPED = 'STOPPED'
    STARTING = 'STARTING'
    PLAYING = 'PLAYING'
    PAUSED = 'PAUSED'
    ERROR = 'ERROR'


class TransitionError(ValueError):
    """The requested change is not allowed in the current state."""
    pass


@dataclass(frozen=True)
class PlayerState:
    """Immutable player state; see the module docstring."""
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
        """A disc with these track numbers was inserted; its first track is selected."""
        if self.mode in (Mode.STARTING, Mode.PLAYING, Mode.PAUSED):
            raise TransitionError('Cannot change disc during active playback')
        if not numbers:
            raise TransitionError('Empty audio disc')
        return PlayerState(Mode.STOPPED, tuple(numbers), numbers[0])

    def disc_removed(self) -> 'PlayerState':
        """The disc is gone."""
        if self.mode in (Mode.STARTING, Mode.PLAYING, Mode.PAUSED):
            raise TransitionError('Cannot remove disc logically while playing; handle transport first')
        return PlayerState()

    def select(self, track: int) -> 'PlayerState':
        """Select a track (only while stopped)."""
        self._require_stopped()
        if track not in self.track_numbers:
            raise TransitionError(f'Track {track} not present')
        return replace(self, selected_track=track)

    def next_track(self) -> 'PlayerState':
        """Select the next track (only while stopped)."""
        self._require_stopped()
        index = self.track_numbers.index(self.selected_track)
        return replace(self, selected_track=self.track_numbers[min(index + 1, len(self.track_numbers)-1)])

    def previous_track(self) -> 'PlayerState':
        """Select the previous track (only while stopped)."""
        self._require_stopped()
        index = self.track_numbers.index(self.selected_track)
        return replace(self, selected_track=self.track_numbers[max(index - 1, 0)])

    def start_requested(self) -> 'PlayerState':
        """PLAY was sent to Sonos; waiting for confirmation."""
        self._require_stopped()
        return replace(self, mode=Mode.STARTING)

    def playback_confirmed(self) -> 'PlayerState':
        """Sonos reports PLAYING."""
        if self.mode != Mode.STARTING:
            raise TransitionError('Playback confirmation expected only after STARTING')
        return replace(self, mode=Mode.PLAYING)

    def paused(self) -> 'PlayerState':
        """Our stream was paused."""
        if self.mode != Mode.PLAYING:
            raise TransitionError('PAUSE requires PLAYING')
        return replace(self, mode=Mode.PAUSED)

    def resumed(self) -> 'PlayerState':
        """Our paused stream plays again."""
        if self.mode != Mode.PAUSED:
            raise TransitionError('RESUME requires PAUSED')
        return replace(self, mode=Mode.PLAYING)

    def followed(self, track: int) -> 'PlayerState':
        """Sonos moved to another track of our disc block; mode unchanged."""
        if self.mode not in (Mode.STARTING, Mode.PLAYING, Mode.PAUSED):
            raise TransitionError('Following a track requires playback')
        if track not in self.track_numbers:
            raise TransitionError(f'Track {track} not present')
        return replace(self, selected_track=track)

    def natural_end(self) -> 'PlayerState':
        """The track (or disc) ended by itself."""
        if self.mode not in (Mode.STARTING, Mode.PLAYING, Mode.PAUSED):
            raise TransitionError('No playback underway')
        return replace(self, mode=Mode.STOPPED)

    def failed(self, message: str) -> 'PlayerState':
        """An error stops playback; ``message`` is shown."""
        if not message.strip():
            raise ValueError('Error message must be nonempty')
        return replace(self, mode=Mode.ERROR, error=message)

    def reset_error(self) -> 'PlayerState':
        """Clear an error (STOP/PLAY at the device)."""
        if self.mode != Mode.ERROR:
            raise TransitionError('Not in ERROR')
        if not self.track_numbers:
            return PlayerState()
        return replace(self, mode=Mode.STOPPED, error=None)

    def _require_stopped(self):
        if self.mode != Mode.STOPPED:
            raise TransitionError(f'Only permitted in STOPPED; current state {self.mode.value}')


def state_for_disc(disc, selected_track: int) -> PlayerState:
    """Initial state for a disc, with ``selected_track`` selected.

    ``disc`` is the table of contents. The track number is checked against
    it, so that no unknown track number can reach a Sonos command.

    Raises:
        TransitionError: the track is not on the disc.
        KeyError/ValueError: from ``disc.get_track`` for an unknown track.
    """
    numbers = tuple(track.number for track in disc.tracks)
    state = PlayerState().disc_loaded(numbers).select(selected_track)
    disc.get_track(state.selected_track)
    return state
