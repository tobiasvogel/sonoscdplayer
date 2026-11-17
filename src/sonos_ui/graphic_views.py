"""ARC-DISPLAY-01C.4/5: opt-in semantic OLED parity projection.

The productive InteractiveMenu.frame path is not switched to this module.
The legacy SSD1327 widgets remain the pixel reference.
"""
from __future__ import annotations

from PIL import Image

from .menu_catalog import NAVIGATION_PAGES
from .multi_render import MultiRenderer
from .navigation_pages import NavigationRenderer
from .semantic_screen import SemanticScreen
from .theme import ssd1327_gray


def _language_pixels(screen: SemanticScreen, renderer: MultiRenderer):
    """Preserve the existing language-picker layout, without any input logic."""
    from .i18n import available_languages
    if screen.kind != "menu" or tuple(item.key for item in screen.items) != tuple(
            available_languages()):
        raise ValueError("language picker items do not match available languages")
    focus = [i for i, item in enumerate(screen.items) if item.focused]
    if len(focus) != 1:
        raise ValueError("language picker needs exactly one focused item")
    t = renderer.theme
    im = Image.new("RGB", (128, 128), t.background)
    renderer.shell(im, renderer.tr("menu.language"))
    for slot, index in enumerate(renderer.menu_window(len(screen.items), focus[0])):
        renderer.menu_row(im, slot, screen.items[index].label,
                          selected=index == focus[0])
    renderer.bottom(im, status=renderer.tr("status.local_only"),
                    footer=renderer.tr("footer.confirm"))
    # Existing InteractiveMenu.frame passes the unquantized language image.
    return im


def render_graphic_semantic(screen: SemanticScreen, *, section: str,
                            language: str = "de", renderer=None,
                            state=None, searching=False, power_notice=""):
    """Render only explicitly supported common screens with legacy pixels."""
    if not isinstance(screen, SemanticScreen):
        raise TypeError("expected SemanticScreen")

    if section in NAVIGATION_PAGES:
        _, keys = NAVIGATION_PAGES[section]
        if screen.kind != "menu" or tuple(item.key for item in screen.items) != keys:
            raise ValueError("semantic navigation does not match catalog")
        nav = NavigationRenderer() if renderer is None else renderer
        if not isinstance(nav, NavigationRenderer):
            raise TypeError("expected NavigationRenderer")
        return nav.semantic_menu(screen, language=language)

    if section == "network_status":
        nav = NavigationRenderer() if renderer is None else renderer
        if not isinstance(nav, NavigationRenderer):
            raise TypeError("expected NavigationRenderer")
        return nav.semantic_network_status(screen, language=language)

    multi = MultiRenderer(language=language) if renderer is None else renderer
    if not isinstance(multi, MultiRenderer):
        raise TypeError("expected MultiRenderer")
    if section == "targets":
        if state is None:
            raise ValueError("speaker state required")
        return multi.render_multi_semantic(screen, state, searching=searching, gray=True)
    if section == "power":
        return multi.render_power_semantic(screen, notice=power_notice, gray=True)
    if section == "language":
        return _language_pixels(screen, multi)

    raise ValueError("no graphical semantic adapter for this section yet")


def graphic_frame_from_menu(menu, *, now: float):
    """Opt-in parity view of navigation and selection overlays.

    No generic fallback: a notice, WLAN setup or other unsupported overlay
    is not silently rendered as the underlying menu.
    """
    from .interactive_menu import InteractiveMenu
    if not isinstance(menu, InteractiveMenu):
        raise TypeError("expected InteractiveMenu")
    screen = menu.semantic_screen(now=now)
    if screen is None or screen.kind not in ("menu", "idle"):
        raise ValueError("no supported active semantic screen")
    if menu.power_menu:
        section = "power"
    elif menu.language_picker:
        section = "language"
    else:
        section = menu.section
    if section == "power":
        return render_graphic_semantic(
            screen, section=section, language=menu.language,
            renderer=menu.renderer, power_notice=menu.power_notice)
    if section == "language":
        return render_graphic_semantic(
            screen, section=section, language=menu.language,
            renderer=menu.renderer)
    if section == "targets":
        return render_graphic_semantic(
            screen, section=section, language=menu.language,
            renderer=menu.renderer, state=menu.state, searching=menu.searching())
    if section in NAVIGATION_PAGES or section == "network_status":
        return render_graphic_semantic(
            screen, section=section, language=menu.language,
            renderer=menu._navigation_renderer)
    raise ValueError("preview does not support this active screen")
