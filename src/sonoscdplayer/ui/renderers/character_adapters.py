"""Read-only adapters from existing navigation and text-input controllers.

No new encoder state machine: InteractiveMenu and TextEntry remain the only
owners of focus, selection, validation and confirmation.
"""
from __future__ import annotations

from sonoscdplayer.ui.renderers.character_layout import portable_text, render_character_screen
from sonoscdplayer.ui.displays.backend import CharacterFrame, validate_frame
from sonoscdplayer.ui.displays.capabilities import DisplayCapabilities
from sonoscdplayer.ui.i18n import Translator
from sonoscdplayer.ui.screens.semantic_screen import ScreenItem, SemanticScreen
# The screen builders are display-independent; re-exported for callers.
from sonoscdplayer.ui.screens.semantic_views import (  # noqa: F401
    language_menu_screen, navigation_screen, network_status_screen,
    power_menu_screen, speaker_selection_screen,
)
from sonoscdplayer.ui.menu.text_input import CANCEL, DELETE, DONE, TextEntry

_ACTION_KEYS = {DONE: "textinput.done", DELETE: "textinput.delete",
                CANCEL: "textinput.cancel"}


def text_entry_frame(entry: TextEntry, capabilities: DisplayCapabilities,
                     *, title: str, language: str = "de",
                     subtitle: str = "", page: int = 0) -> CharacterFrame:
    """A tiny-screen projection of the existing WLAN text-entry state.

    The controller still owns the 95-character wheel, fast turning, UTF-8
    byte limits, editing, validation and submission. No new input actions.
    """
    if not isinstance(entry, TextEntry):
        raise TypeError("expected TextEntry")
    if not isinstance(capabilities, DisplayCapabilities) or capabilities.kind != "character":
        raise ValueError("character capabilities required")
    width, height = capabilities.columns, capabilities.rows
    if width < 8 or height < 2:
        raise ValueError("text entry requires at least 8 columns and 2 rows")
    if type(page) is not int or page < 0:
        raise ValueError("page must be nonnegative")
    tr = Translator(language).tr
    current = entry.current
    if current in _ACTION_KEYS:
        choice = tr(_ACTION_KEYS[current])
    elif current == " ":
        choice = tr("textinput.space")
    else:
        choice = current

    def fit(s: str) -> str:
        return portable_text(s)[:width].ljust(width)

    # Tail preserves the current insertion position at the right edge.
    # A single terminal bar indicates the active cursor after the value.
    tail = portable_text(entry.text)[-(width - 1):] + "|"
    count = f"{entry.byte_length}/{entry.max_bytes}"
    if entry.error:
        key, params = entry.error
        diagnostic = tr(key, **params)
    else:
        diagnostic = count

    if height == 2:
        # Every view retains the action wheel on row 2. With an error we
        # alternate error/text/context; otherwise text/context alternates.
        if entry.error:
            top = (diagnostic, tail, subtitle or title)[page % 3]
        else:
            top = (tail, subtitle or title)[page % 2]
        lines = [fit(top), fit("> " + choice)]
    elif height == 3:
        lines = [fit(title), fit(diagnostic) if entry.error else fit(tail),
                 fit("> " + choice)]
    else:
        lines = [fit(subtitle if subtitle and page % 2 else title),
                 fit(tail), fit(diagnostic), fit("> " + choice)]
        if height > 4 and subtitle:
            lines.append(fit(subtitle))
    lines.extend([" " * width] * (height - len(lines)))
    frame = CharacterFrame(tuple(lines[:height]))
    validate_frame(capabilities, frame)
    return frame


def render_navigation(section: str, focus: int,
                      capabilities: DisplayCapabilities, *,
                      language: str = "de", scroll_offset: int = 0) -> CharacterFrame:
    return render_character_screen(
        navigation_screen(section, focus, language=language),
        capabilities, scroll_offset=scroll_offset)


def wifi_choice_screen(controller, snapshot, *, language: str = "de") -> SemanticScreen:
    """WLAN choices exactly as WifiEntryController.event interprets them.

    The current controller includes RESCAN, HIDDEN and BACK after networks.
    Its item order is authoritative, never a renderer-specific menu.
    """
    from sonoscdplayer.ui.menu import wifi_entry
    from sonoscdplayer.ui.menu.wifi_entry import WifiEntryController, HIDDEN, BACK
    from sonoscdplayer.network_setup.session import SetupSnapshot
    if not isinstance(controller, WifiEntryController) or not isinstance(snapshot, SetupSnapshot):
        raise TypeError("expected WLAN UI controller and setup snapshot")
    if snapshot.state != "choosing" or controller.mode != "list":
        raise ValueError("WLAN choice list is not active")
    tr = Translator(language).tr
    choices = controller.items(snapshot)
    if type(controller.focus) is not int or not 0 <= controller.focus < len(choices):
        raise ValueError("invalid WLAN menu focus")
    items = []
    for index, choice in enumerate(choices):
        if choice is getattr(wifi_entry, "RESCAN", None):
            key, label, selected, intent = (
                "wifi:rescan", tr("netsetup.rescan"), False, "rescan_networks")
        elif choice is HIDDEN:
            key, label, selected, intent = (
                "wifi:hidden", tr("netsetup.hidden"), False, "hidden_network")
        elif choice is BACK:
            key, label, selected, intent = (
                "wifi:back", tr("netsetup.back_hotspot"), False, "back_to_hotspot")
        else:
            key, label, selected, intent = (
                f"wifi:{index}", choice.ssid, choice.ssid in snapshot.saved,
                "choose_network")
        items.append(ScreenItem(key, label, focused=index == controller.focus,
                                selected=selected, intent=intent))
    return SemanticScreen("menu", heading=tr("netsetup.choose"), items=tuple(items))
