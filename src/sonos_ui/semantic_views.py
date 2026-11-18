"""ARC-DISPLAY-01D: display-independent screen builders.

Each function turns controller state into a translated SemanticScreen. They
read state only: no input handling, no player commands, no pixels. Every
display type (SSD1327 today, character LCDs later) draws from these.
"""
from __future__ import annotations

from .i18n import Translator
from .menu_catalog import POWER_ITEMS, navigation_page
from .notice import Notice
from .semantic_screen import ScreenItem, SemanticScreen, screen_from_notice

SYSTEM_SCREEN_KEYS = {
    "transition": "system.shutting_down",
    "wait": "system.shutting_down",
    "reboot": "system.restarting",
    "final": "system.please_wait",
    "safe": "system.safe_to_unplug",
    "start": "system.starting",
}


def navigation_screen(section: str, focus: int, *, language: str = "de") -> SemanticScreen:
    """One of the catalog pages (main, network, settings)."""
    heading, item_keys = navigation_page(section)
    if type(focus) is not int or not 0 <= focus < len(item_keys):
        raise ValueError("focus outside navigation menu")
    tr = Translator(language).tr
    return SemanticScreen(
        "menu", heading=tr(heading), view="navigation",
        items=tuple(ScreenItem(key=key, label=tr(key), focused=i == focus,
                               intent=key)
                    for i, key in enumerate(item_keys)),
        status=tr("status.local_only"), footer=tr("footer.select"))


def speaker_selection_screen(state, *, language: str = "de",
                             searching: bool = False) -> SemanticScreen:
    """Speaker list including Apply and Back, as MultiSelection holds it."""
    from .multi_target import MultiSelection
    if not isinstance(state, MultiSelection):
        raise TypeError("expected MultiSelection")
    tr = Translator(language).tr
    targets = state.topology.targets
    items = [
        ScreenItem(key=target.key, label=target.label,
                   selected=target.key in state.draft, focused=i == state.focus,
                   intent="toggle_target")
        for i, target in enumerate(targets)
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
    if state.notice:
        status = tr(state.notice)
    else:
        status = tr("status.incomplete" if state.topology.error else "status.no_changes")
    placeholder = ""
    if not targets:
        placeholder = tr("status.scan_running" if searching else "status.no_targets")
    return SemanticScreen(
        "menu", heading=tr("menu.targets"), items=tuple(items), view="targets",
        subheading=tr("status.targets", selected=len(state.draft), total=n),
        placeholder=placeholder, status=status, footer=tr("footer.mark"))


def power_menu_screen(focus: int, *, notice: str = "",
                      language: str = "de") -> SemanticScreen:
    """POWER overlay contents only; no POWER action or confirmation here."""
    if type(focus) is not int or not 0 <= focus < len(POWER_ITEMS):
        raise ValueError("invalid power focus")
    tr = Translator(language).tr
    return SemanticScreen(
        "menu", heading=tr("menu.power"), view="power",
        items=tuple(ScreenItem(key, tr(key), focused=i == focus, intent=key)
                    for i, key in enumerate(POWER_ITEMS)),
        status=tr(notice or "status.power_choose"), footer=tr("footer.select"))


def language_menu_screen(languages: tuple[str, ...], focus: int,
                         *, language: str = "de") -> SemanticScreen:
    """Existing language-picker state, not an independent picker."""
    tr = Translator(language)
    if not isinstance(languages, tuple) or not languages or (
            type(focus) is not int or not 0 <= focus < len(languages)):
        raise ValueError("invalid language picker")
    return SemanticScreen(
        "menu", heading=tr.tr("menu.language"), view="language",
        items=tuple(ScreenItem(key=code, label=tr.language_name(code),
                               focused=i == focus, selected=code == tr.language,
                               intent="choose_language")
                    for i, code in enumerate(languages)),
        status=tr.tr("status.local_only"), footer=tr.tr("footer.confirm"))


def network_status_screen(snapshot, *, language: str = "de") -> SemanticScreen:
    """Read-only network status."""
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
        "idle", heading=tr("network.status"), view="network_status",
        items=tuple(ScreenItem(k, tr(k), value=tr(v)) for k, v in pairs),
        status=tr("network.readonly"), footer=tr("footer.select"))


def volume_screen(level: int, *, language: str = "de") -> SemanticScreen:
    """Short volume overlay."""
    if type(level) is not int or not 0 <= level <= 100:
        raise ValueError("volume must be an integer from 0 to 100")
    return SemanticScreen("idle", view="volume", items=(
        ScreenItem("volume", Translator(language).tr("menu.volume"), value=str(level)),))


def notice_screen(notice: Notice, *, language: str = "de",
                  dismissable: bool = False) -> SemanticScreen:
    """A notice; ``footer`` tells how to close it when it stays up."""
    screen = screen_from_notice(notice, language=language)
    if not dismissable:
        return screen
    return SemanticScreen("notice", message=screen.message, severity=screen.severity,
                          view="notice",
                          footer=Translator(language).tr("notice.dismiss_hint"))


def system_screen(name: str, *, language: str = "de") -> SemanticScreen:
    """Shutdown/restart/start screens."""
    try:
        key = SYSTEM_SCREEN_KEYS[name]
    except KeyError as exc:
        raise ValueError("unknown shutdown screen") from exc
    return SemanticScreen("notice", message=Translator(language).tr(key),
                          severity="info", view="system")


def with_feedback(screen: SemanticScreen, notice: Notice, *,
                  language: str = "de") -> SemanticScreen:
    """Attach short feedback to a menu (shown full-screen on small displays)."""
    from dataclasses import replace
    feedback = screen_from_notice(notice, language=language)
    return replace(screen, message=feedback.message, severity=feedback.severity)
