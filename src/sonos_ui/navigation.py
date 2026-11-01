"""Pure UI navigation; never discovers speakers or issues player commands.

Input events are semantic actions, independent of GPIO/keyboard/touch adapters.
Selection is only an intent until a separate application layer validates it.
"""
from dataclasses import dataclass, replace
from typing import Literal

from .model import DisplayModel

Action = Literal['navigate_next', 'navigate_previous', 'confirm', 'back', 'open_speakers']


@dataclass(frozen=True)
class NavigationState:
    """View state independent of transport and display-driver lifetime."""
    menu_open: bool = False
    focus_index: int = 0
    view_started_ms: int = 0
    focus_started_ms: int = 0


@dataclass(frozen=True)
class SelectionIntent:
    """A selection request, not an accepted speaker or a Sonos side effect."""
    speaker_index: int
    speaker_name: str


@dataclass(frozen=True)
class NavigationResult:
    navigation: NavigationState
    intent: SelectionIntent | None = None


def current_view(display: DisplayModel, navigation: NavigationState) -> str:
    if navigation.menu_open:
        return 'menu'
    return 'playback' if display.playback in ('playing', 'paused') else 'idle'


def step(display: DisplayModel, navigation: NavigationState, action: Action,
         *, now_ms: int) -> NavigationResult:
    """Process one action; no persistent speaker selection happens here."""
    if now_ms < 0:
        raise ValueError('now_ms must be nonnegative')
    if action not in ('navigate_next', 'navigate_previous', 'confirm', 'back', 'open_speakers'):
        raise ValueError('unknown UI action')
    if action == 'back':
        return NavigationResult(replace(navigation, menu_open=False, view_started_ms=now_ms))
    if action == 'open_speakers' or (action == 'confirm' and not navigation.menu_open):
        return NavigationResult(replace(navigation, menu_open=True, view_started_ms=now_ms,
                                        focus_started_ms=now_ms,
                                        focus_index=min(max(0,navigation.focus_index),max(0,len(display.speakers)-1))))
    if not navigation.menu_open:
        return NavigationResult(navigation)
    n = len(display.speakers)
    if action in ('navigate_next', 'navigate_previous'):
        if n == 0:
            return NavigationResult(replace(navigation, focus_index=0))
        delta = 1 if action == 'navigate_next' else -1
        index = max(0, min(n-1, navigation.focus_index + delta))
        return NavigationResult(replace(navigation, focus_index=index,
                                        focus_started_ms=now_ms if index != navigation.focus_index else navigation.focus_started_ms))
    if action == 'confirm':
        if not 0 <= navigation.focus_index < n:
            return NavigationResult(navigation)
        index = navigation.focus_index
        return NavigationResult(navigation, SelectionIntent(index, display.speakers[index]))
    return NavigationResult(navigation)


def selection_acknowledged(navigation: NavigationState, *, now_ms: int) -> NavigationState:
    """Caller may close the menu only after its own authoritative validation."""
    return replace(navigation, menu_open=False, view_started_ms=now_ms)


def project(display: DisplayModel, navigation: NavigationState) -> DisplayModel:
    """Prepare a render-only snapshot; do not mutate domain state."""
    index = max(0, min(navigation.focus_index, max(0,len(display.speakers)-1)))
    return replace(display, view=current_view(display, navigation), focus_index=index)
