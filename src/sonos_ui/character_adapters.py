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
    elif height == 3:
        lines = [fit(title), fit(diagnostic) if entry.error else fit(tail),
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


def speaker_selection_screen(state, *, language: str = "de") -> SemanticScreen:
    """Read-only projection of MultiSelection including Apply and Back.

    Distinguishes draft checkmarks from focus and never touches the topology
    or requests a player write. Uses existing stable target keys.
    """
    from .multi_target import MultiSelection
    if not isinstance(state, MultiSelection):
        raise TypeError("expected MultiSelection")
    tr = Translator(language).tr
    items = [
        ScreenItem(key=target.key, label=target.label,
                   selected=target.key in state.draft, focused=i == state.focus,
                   intent="toggle_target")
        for i, target in enumerate(state.topology.targets)
    ]
    n = len(items)
    items.extend((
        ScreenItem(key="menu.review", label=tr("menu.review"),
                   focused=state.focus == n,
                   enabled=not state.topology.error and bool(state.draft),
                   intent="review_target"),
        ScreenItem(key="menu.back", label=tr("menu.back"),
                   focused=state.focus >= n + 1, intent="back"),
    ))
    return SemanticScreen("menu", heading=tr("menu.targets"), items=tuple(items))


def power_menu_screen(focus: int, *, language: str = "de") -> SemanticScreen:
    """POWER overlay contents only; no POWER action or confirmation here."""
    keys = ("menu.back", "menu.poweroff", "menu.reboot")
    if type(focus) is not int or not 0 <= focus < len(keys):
        raise ValueError("invalid power focus")
    tr = Translator(language).tr
    return SemanticScreen("menu", heading=tr("menu.power"),
                          items=tuple(ScreenItem(key, tr(key), focused=i == focus,
                                                 intent=key)
                                      for i, key in enumerate(keys)))


def language_menu_screen(languages: tuple[str, ...], focus: int,
                         *, language: str = "de") -> SemanticScreen:
    """Existing language-picker state, not an independent picker."""
    tr = Translator(language)
    if not isinstance(languages, tuple) or not languages or (
            type(focus) is not int or not 0 <= focus < len(languages)):
        raise ValueError("invalid language picker")
    return SemanticScreen(
        "menu", heading=tr.tr("menu.language"),
        items=tuple(ScreenItem(key=code, label=tr.language_name(code),
                               focused=i == focus, selected=code == tr.language,
                               intent="choose_language")
                    for i, code in enumerate(languages)))


def wifi_choice_screen(controller, snapshot, *, language: str = "de") -> SemanticScreen:
    """WLAN choices exactly as WifiEntryController.event interprets them.

    Unlike the older graphic list, do not invent an extra 'rescan' entry:
    the live controller supports networks, HIDDEN and BACK only.
    """
    from .wifi_entry import WifiEntryController, HIDDEN, BACK
    from network_setup.session import SetupSnapshot
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
        if choice is HIDDEN:
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


def network_status_screen(snapshot, *, language: str = "de") -> SemanticScreen:
    """Read-only network status; translating keys follows the OLED page."""
    from .network_status import NetworkStatus
    if not isinstance(snapshot, NetworkStatus):
        raise TypeError("expected NetworkStatus")
    tr = Translator(language).tr
    pairs = (
        ("network.ethernet", "network.state." + snapshot.ethernet),
        ("network.wifi", "network.state." + snapshot.wifi),
        ("network.preferred", "network.preferred." + snapshot.preferred),
    )
    return SemanticScreen(
        "idle", heading=tr("network.status"),
        items=tuple(ScreenItem(k, tr(k), value=tr(v)) for k, v in pairs))
