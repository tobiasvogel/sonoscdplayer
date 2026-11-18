"""Hardware-independent definitions for the ordinary navigation pages.

ARC-DISPLAY-01C/01D: a single source for menu order and heading keys.
The controller retains ownership of input and action execution. Renderer
modules consume this catalog but do not redefine the menu content.
"""
from types import MappingProxyType


MAIN_ITEMS = ("menu.targets", "menu.network", "menu.settings", "menu.back")
NETWORK_ITEMS = ("network.status", "network.setup", "menu.back")
SETTINGS_ITEMS = ("menu.language", "menu.rescan", "menu.back")

# POWER overlay, in display order (the controller acts on the part after "menu.").
POWER_ITEMS = ("menu.back", "menu.poweroff", "menu.reboot")

# Each entry is (localized heading key, ordered localized item keys).
# Immutable to prevent renderers from accidentally changing controller order.
NAVIGATION_PAGES = MappingProxyType({
    "main": ("menu.main", MAIN_ITEMS),
    "network": ("menu.network", NETWORK_ITEMS),
    "settings": ("menu.settings", SETTINGS_ITEMS),
})


def navigation_page(section: str) -> tuple[str, tuple[str, ...]]:
    """Return the common page descriptor; unknown sections are not menus."""
    try:
        return NAVIGATION_PAGES[section]
    except (KeyError, TypeError) as exc:
        raise ValueError("unsupported navigation section") from exc
