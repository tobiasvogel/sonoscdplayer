"""Read-only character display projection of the existing OLED menu priorities.

This module does not route input or own a second state machine. Callers supply
a monotonic timestamp and the same InteractiveMenu instance used by the OLED.
Automatic pagination is *display only*: encoder presses remain governed by
InteractiveMenu.event and NoticeTiming. Which screen is shown is decided by
InteractiveMenu.semantic_screen(), the same source the OLED draws from.
"""
from __future__ import annotations

from sonoscdplayer.ui.renderers.character_adapters import text_entry_frame, wifi_choice_screen
from sonoscdplayer.ui.renderers.character_layout import character_page_count, render_character_screen
from sonoscdplayer.ui.displays.capabilities import DisplayCapabilities
from sonoscdplayer.ui.i18n import Translator
from sonoscdplayer.ui.notices.notice import Notice
from sonoscdplayer.ui.notices.policy import NoticeTiming
from sonoscdplayer.ui.screens.semantic_screen import ScreenItem, SemanticScreen
from sonoscdplayer.ui.screens.semantic_views import notice_screen

PAGE_SECONDS = 3.0


def _page(now: float, count: int) -> int:
    if not isinstance(now, (int, float)) or not 0 <= now < float("inf"):
        raise ValueError("now must be finite and nonnegative")
    return int(now // PAGE_SECONDS) % count


def _render(screen, capabilities, now):
    return render_character_screen(
        screen, capabilities,
        page=_page(now, character_page_count(screen, capabilities)),
        scroll_offset=int(now * 2) % 96)


def _notice_frame(notice: Notice, capabilities, now, language):
    # A notice only advertises confirmation when the live policy permits it.
    screen = notice_screen(notice, language=language,
                           dismissable=NoticeTiming().encoder_can_confirm(notice))
    return _render(screen, capabilities, now)


def _setup_frame(snapshot, capabilities, now, language):
    tr = Translator(language).tr
    if snapshot.state == "hotspot":
        # Rotate complete label/value pairs. Unlike a single 'label: value'
        # line this never clips the *start* of the password or SSID on 16x2.
        fields = (
            ("SSID", snapshot.hotspot_ssid),
            (tr("netsetup.password"), snapshot.password),
            ("URL", snapshot.url.replace("http://", "")),
            (tr("netsetup.remaining",
                minutes=max(1, (snapshot.remaining + 59) // 60)),
             tr("netsetup.manual_hint")),
            (tr("netsetup.cancel_hint"), ""),
        )
        label, value = fields[_page(now, len(fields))]
        if capabilities.rows == 2:
            screen = SemanticScreen("idle", items=(
                ScreenItem("label", label),
                ScreenItem("value", value or " ")))
        else:
            screen = SemanticScreen("idle", items=(
                ScreenItem("label", tr("netsetup.title")),
                ScreenItem("name", label),
                ScreenItem("value", value or " "),
                ScreenItem("cancel", tr("netsetup.cancel_hint"))))
        # Keep an action-only instruction anchored at its beginning.
        # Scroll long values (SSID/URL) and the manual-entry hint instead.
        offset = int(now * 2) % 96 if value else 0
        return render_character_screen(
            screen, capabilities, scroll_offset=offset)
    key = {
        "preparing": "netsetup.preparing",
        "scanning": "netsetup.scanning",
        "unavailable": ("netsetup.no_wifi" if snapshot.error == "no_wifi"
                        else "netsetup.hotspot_failed"),
    }.get(snapshot.state)
    if key is not None:
        text = tr(key)
    elif snapshot.state in ("connecting", "connected"):
        key = "netsetup.connecting" if snapshot.state == "connecting" else "netsetup.connected"
        text = tr(key, ssid=snapshot.target)
    else:
        raise ValueError("unsupported WLAN setup state")
    return _render(SemanticScreen("notice", message=text,
                                 severity="error" if snapshot.state == "unavailable"
                                 else "info"), capabilities, now)


def character_frame_from_menu(menu, capabilities: DisplayCapabilities,
                              *, now: float, base_screen: SemanticScreen | None = None):
    """Mirror active InteractiveMenu screen precedence without processing input.

    An inactive menu requires a caller-provided idle/playback screen from the
    existing view model; it is unsafe to invent album/track information.
    """
    from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu

    if not isinstance(menu, InteractiveMenu):
        raise TypeError("expected InteractiveMenu")
    if not isinstance(capabilities, DisplayCapabilities) or capabilities.kind != "character":
        raise ValueError("character display capabilities required")
    _page(now, 1)  # Validate the timestamp even for non-paged views.
    # The controller is the common source of truth for all normal views.
    # Only specialised WLAN screens remain in this character adapter.
    semantic = menu.semantic_screen(now=now)
    if semantic is not None:
        return _render(semantic, capabilities, now)
    tr = Translator(menu.language).tr

    setup = menu.netsetup_snapshot()
    if setup is not None and not menu.power_menu:
        if setup.state == "choosing":
            wifi = menu.wifi_entry
            if wifi is None:
                raise ValueError("no WLAN entry controller")
            if wifi.mode == "list":
                if wifi.list_notice:
                    # Keep the actionable list visible on alternate views.
                    if _page(now, 2) == 0:
                        return _notice_frame(
                            Notice(wifi.list_notice, severity="error"),
                            capabilities, now, menu.language)
                return render_character_screen(
                    wifi_choice_screen(wifi, setup, language=menu.language),
                    capabilities, scroll_offset=int(now * 2) % 96)
            if wifi.mode in ("ssid", "password"):
                if wifi.entry is None:
                    raise ValueError("WLAN entry state missing")
                return text_entry_frame(
                    wifi.entry, capabilities,
                    title=tr("netsetup.ssid_title" if wifi.mode == "ssid"
                             else "netsetup.password_title"),
                    subtitle=wifi.target if wifi.mode == "password" else "",
                    language=menu.language, page=_page(now, 6))
            raise ValueError("unsupported WLAN controller mode")
        return _setup_frame(setup, capabilities, now, menu.language)

    if not isinstance(base_screen, SemanticScreen) or base_screen.kind not in (
            "idle", "playback"):
        raise ValueError("inactive menu requires an idle/playback base screen")
    return _render(base_screen, capabilities, now)
