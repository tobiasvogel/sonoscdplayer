"""ARC-DISPLAY-01C.4: opt-in, offscreen OLED projection from SemanticScreen.

This adapter deliberately supports only the navigation and network-status
views whose existing pixel layout can be reproduced exactly from semantic
fields. The productive InteractiveMenu.frame path remains untouched.
"""
from __future__ import annotations

from .menu_catalog import NAVIGATION_PAGES
from .navigation_pages import NavigationRenderer
from .semantic_screen import SemanticScreen


def render_graphic_semantic(screen: SemanticScreen, *, section: str,
                            language: str = "de", renderer=None):
    """Draw a validated semantic screen using the existing SSD1327 routines.

    No input handling, network calls, GPIO, target control or new canvas
    design. Other screen kinds are explicitly deferred rather than drawing
    a misleading generic representation.
    """
    if not isinstance(screen, SemanticScreen):
        raise TypeError("expected SemanticScreen")
    renderer = NavigationRenderer() if renderer is None else renderer
    if not isinstance(renderer, NavigationRenderer):
        raise TypeError("expected NavigationRenderer")
    if section in NAVIGATION_PAGES:
        expected_heading, keys = NAVIGATION_PAGES[section]
        if screen.kind != "menu" or tuple(item.key for item in screen.items) != keys:
            raise ValueError("semantic navigation does not match catalog")
        return renderer.semantic_menu(screen, language=language)
    if section == "network_status":
        return renderer.semantic_network_status(screen, language=language)
    raise ValueError("no graphical semantic adapter for this section yet")


def graphic_frame_from_menu(menu, *, now: float):
    """Preview active navigation/status pixels from the common controller.

    This function is opt-in and does not replace InteractiveMenu.frame.
    """
    from .interactive_menu import InteractiveMenu
    if not isinstance(menu, InteractiveMenu):
        raise TypeError("expected InteractiveMenu")
    if menu.section not in NAVIGATION_PAGES and menu.section != "network_status":
        raise ValueError("preview only supports navigation and network status")
    screen = menu.semantic_screen(now=now)
    if screen is None:
        raise ValueError("no active semantic menu screen")
    return render_graphic_semantic(
        screen, section=menu.section, language=menu.language,
        renderer=menu._navigation_renderer)
