"""Read-only adapters from existing navigation and text-input controllers.

No new encoder state machine: InteractiveMenu and TextEntry remain the only
owners of focus, selection, validation and confirmation.
"""
from __future__ import annotations

from .character_layout import portable_text, render_character_screen
from .display_backend import CharacterFrame, validate_frame
from .display_capabilities import DisplayCapabilities
from .i18n import Translator
from .navigation_pages import MAIN_ITEMS, NETWORK_ITEMS, SETTINGS_ITEMS
from .semantic_screen import ScreenItem, SemanticScreen
from .text_input import CANCEL, DELETE, DONE, TextEntry

_PAGE_KEYS = {"main": ("menu.main", MAIN_ITEMS),
              "network": ("menu.network", NETWORK_ITEMS),
              "settings": ("menu.settings", SETTINGS_ITEMS)}
_ACTION_KEYS = {DONE: "textinput.done", DELETE: "textinput.delete",
                CANCEL: "textinput.cancel"}


def navigation_screen(section: str, focus: int, *, language: str = "de") -> SemanticScreen:
    """Project the live menu's existing item ordering, without driving it."""
    try:
        heading, item_keys = _PAGE_KEYS[section]
    except (KeyError, TypeError) as exc:
        raise ValueError("unsupported navigation section") from exc
    if type(focus) is not int or not 0 <= focus < len(item_keys):
        raise ValueError("focus outside navigation menu")
    tr = Translator(language).tr
    return SemanticScreen(
        "menu", heading=tr(heading),
        items=tuple(ScreenItem(key=key, label=tr(key), focused=i == focus,
                               intent=key)
                    for i, key in enumerate(item_keys)))


def text_entry_frame(entry: TextEntry, capabilities: DisplayCapabilities,
                     *, title: str, language: str = "de",
                     subtitle: str = "") -> CharacterFrame:
    """A tiny-screen projection of the existing D-224 input state.

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
        # Errors temporarily occupy the first row, not the selected action.
        lines = [fit(diagnostic) if entry.error else fit(tail),
                 fit("> " + choice)]
    else:
        lines = [fit(title), fit(tail), fit(diagnostic),
                 fit("> " + choice)]
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
