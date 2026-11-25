"""Draw a SemanticScreen on the 128x128 SSD1327 OLED.

InteractiveMenu.semantic_screen() decides WHAT is shown (and in which order
overlays win); this module only decides HOW it looks on this display. The
widgets are the existing Modern Hi-Fi ones, so the pixels do not change.
"""
from __future__ import annotations

from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen

_TARGET_MARKS = {"review_target": ">", "back": "<"}


def _focus(screen: SemanticScreen) -> int:
    for index, item in enumerate(screen.items):
        if item.focused:
            return index
    raise ValueError("menu needs a focused item")


def render_screen(screen: SemanticScreen, *, renderer, navigation_renderer=None,
                  notice_renderer=None, gray: bool = True):
    """Pixels for one semantic screen.

    ``renderer`` is the MultiRenderer of the menu; the navigation and notice
    renderers are created on demand when not given.
    """
    if not isinstance(screen, SemanticScreen):
        raise TypeError("expected SemanticScreen")
    view = screen.view
    if view == "navigation":
        from sonoscdplayer.ui.menu.navigation_pages import NavigationRenderer
        nav = navigation_renderer or NavigationRenderer(language=renderer.translator.language)
        return nav.menu_pixels(screen.heading,
                               [(item.key, item.label) for item in screen.items],
                               _focus(screen), status=screen.status, footer=screen.footer,
                               **({} if gray else {'gray': False}))
    if view == "network_status":
        from sonoscdplayer.ui.menu.navigation_pages import NavigationRenderer
        nav = navigation_renderer or NavigationRenderer(language=renderer.translator.language)
        return nav.status_pixels(screen.heading,
                                 [(item.label, item.value or "") for item in screen.items],
                                 status=screen.status, footer=screen.footer,
                                 **({} if gray else {'gray': False}))
    if view == "targets":
        marks = [_TARGET_MARKS.get(item.intent) or ("✓" if item.selected else "□")
                 for item in screen.items]
        return renderer.multi_pixels(
            screen.heading, screen.subheading, [item.label for item in screen.items],
            marks, _focus(screen), placeholder=screen.placeholder,
            status=screen.status, footer=screen.footer, gray=gray)
    if view == "power":
        return renderer.power_pixels(screen.heading, [item.label for item in screen.items],
                                     _focus(screen), status=screen.status,
                                     footer=screen.footer, gray=gray)
    if view == "language":
        return renderer.language_pixels(screen.heading,
                                        [item.label for item in screen.items],
                                        _focus(screen), status=screen.status,
                                        footer=screen.footer)
    if view == "volume":
        return renderer.render_volume(int(screen.items[0].value), gray=gray)
    if view == "notice":
        from sonoscdplayer.ui.notices.render import NoticeRenderer
        notices = notice_renderer or NoticeRenderer()
        return notices.render_text(screen.message, screen.severity, grayscale=gray,
                                   hint=screen.footer)
    raise ValueError(f"no OLED widget for view {view!r}")


def graphic_frame_from_menu(menu, *, now: float, gray=True):
    """The OLED image of an InteractiveMenu (shutdown screens excluded).

    WLAN setup still has its own renderer; an inactive menu without
    overlay shows the speaker list, as before.
    """
    screen = menu.semantic_screen(now=now)
    if screen is not None:
        return render_screen(screen, renderer=menu.renderer,
                             navigation_renderer=menu._navigation_renderer,
                             notice_renderer=menu._connect_renderer, gray=gray)
    setup = menu.netsetup_snapshot()
    if setup is not None and not menu.power_menu:
        return menu.wifi_entry.frame(menu.renderer, setup, now=now, gray=gray)
    return menu.renderer.render_multi(menu.state, gray=gray, searching=menu.searching())
