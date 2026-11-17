"""OLED-only navigation pages; no network, GPIO or player side effects."""
from PIL import Image
from .render import Renderer
from .theme import ssd1327_gray

from .menu_catalog import MAIN_ITEMS, NETWORK_ITEMS, SETTINGS_ITEMS


class NavigationRenderer(Renderer):
    def _menu_pixels(self, heading, items, focus):
        """One pixel layout for legacy navigation and semantic navigation."""
        t = self.theme
        image = Image.new("RGB", (128, 128), t.background)
        self.shell(image, heading)
        for slot, index in enumerate(self.menu_window(len(items), focus)):
            key, label = items[index]
            marker = "<" if key == "menu.back" else ">"
            self.menu_row(image, slot, label, selected=index == focus,
                          marker=marker)
        self.bottom(image, status=self.tr("status.local_only"),
                    footer=self.tr("footer.select"))
        return ssd1327_gray(image)

    def page(self, heading, items, focus, *, language="de"):
        """Existing OLED API kept intact for production callers."""
        from .i18n import Translator
        tr = Translator(language).tr
        return self._menu_pixels(tr(heading), [(key, tr(key)) for key in items], focus)

    def semantic_menu(self, screen):
        """Render a navigation SemanticScreen using the identical pixel path.

        The semantic item keys control marker choice, not execution. Only
        navigation pages are accepted here; other UI kinds need their own
        specialised graphical widgets.
        """
        from .semantic_screen import SemanticScreen
        if not isinstance(screen, SemanticScreen) or screen.kind != "menu":
            raise ValueError("semantic navigation menu required")
        if sum(item.focused for item in screen.items) != 1:
            raise ValueError("navigation menu needs exactly one focused item")
        return self._menu_pixels(
            screen.heading, [(item.key, item.label) for item in screen.items],
            next(i for i, item in enumerate(screen.items) if item.focused))

    def _status_pixels(self, heading, fields):
        t = self.theme
        image = Image.new("RGB", (128, 128), t.background)
        self.shell(image, heading)
        for index, (label, value) in enumerate(fields):
            y = 23 + index * 25
            self.text(image, (6, y), label, fill=t.secondary, size=9,
                      width=116, height=11)
            self.text(image, (9, y + 11), value, fill=t.primary, size=10,
                      bold=True, width=113, height=13)
        self.bottom(image, status=self.tr("network.readonly"),
                    footer=self.tr("footer.select"))
        return ssd1327_gray(image)

    def network_status(self, snapshot, *, language="de"):
        """Existing OLED API kept intact for production callers."""
        from .i18n import Translator
        tr = Translator(language).tr
        fields = (
            ("network.ethernet", "network.state." + snapshot.ethernet),
            ("network.wifi", "network.state." + snapshot.wifi),
            ("network.preferred", "network.preferred." + snapshot.preferred),
        )
        return self._status_pixels(
            tr("network.status"), [(tr(key), tr(value)) for key, value in fields])

    def semantic_network_status(self, screen):
        """Render the common read-only network-status screen."""
        from .semantic_screen import SemanticScreen
        if not isinstance(screen, SemanticScreen) or screen.kind != "idle":
            raise ValueError("semantic network status required")
        if tuple(item.key for item in screen.items) != (
                "network.ethernet", "network.wifi", "network.preferred"):
            raise ValueError("unexpected network-status fields")
        if any(item.value is None for item in screen.items):
            raise ValueError("network-status values required")
        return self._status_pixels(
            screen.heading, [(item.label, item.value) for item in screen.items])
