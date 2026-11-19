from PIL import Image

from sonoscdplayer.ui.renderers.oled_128 import Renderer
from sonoscdplayer.ui.renderers.theme import ssd1327_gray

from sonoscdplayer.ui.menu.menu_catalog import MAIN_ITEMS, NETWORK_ITEMS, SETTINGS_ITEMS  # noqa: F401


class NavigationRenderer(Renderer):
    def menu_pixels(self, heading, items, focus, *, status, footer):
        """Navigation page from translated texts: items are (key, label)."""
        t = self.theme
        image = Image.new("RGB", (128, 128), t.background)
        self.shell(image, heading)
        for slot, index in enumerate(self.menu_window(len(items), focus)):
            key, label = items[index]
            marker = "<" if key == "menu.back" else ">"
            self.menu_row(image, slot, label, selected=index == focus,
                          marker=marker)
        self.bottom(image, status=status, footer=footer)
        return ssd1327_gray(image)

    def page(self, heading, items, focus, *, language="de"):
        """Navigation page from locale keys."""
        from sonoscdplayer.ui.i18n import Translator
        tr = Translator(language).tr
        return self.menu_pixels(tr(heading), [(key, tr(key)) for key in items],
                                focus, status=tr("status.local_only"),
                                footer=tr("footer.select"))

    def status_pixels(self, heading, fields, *, status, footer):
        """Read-only label/value page from translated texts."""
        t = self.theme
        image = Image.new("RGB", (128, 128), t.background)
        self.shell(image, heading)
        for index, (label, value) in enumerate(fields):
            y = 23 + index * 25
            self.text(image, (6, y), label, fill=t.secondary, size=9,
                      width=116, height=11)
            self.text(image, (9, y + 11), value, fill=t.primary, size=10,
                      bold=True, width=113, height=13)
        self.bottom(image, status=status, footer=footer)
        return ssd1327_gray(image)

    def network_status(self, snapshot, *, language="de"):
        """Network status page from a NetworkStatus snapshot."""
        from sonoscdplayer.ui.i18n import Translator
        tr = Translator(language).tr
        fields = (
            ("network.ethernet", "network.state." + snapshot.ethernet),
            ("network.wifi", "network.state." + snapshot.wifi),
            ("network.preferred", "network.preferred." + snapshot.preferred),
        )
        return self.status_pixels(
            tr("network.status"), [(tr(key), tr(value)) for key, value in fields],
            status=tr("network.readonly"), footer=tr("footer.select"))
