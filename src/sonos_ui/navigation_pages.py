"""OLED-only navigation pages; no network, GPIO or player side effects."""
from PIL import Image
from .render import Renderer
from .theme import ssd1327_gray

MAIN_ITEMS = ("menu.targets", "menu.network", "menu.settings", "menu.back")
NETWORK_ITEMS = ("network.status", "network.setup", "menu.back")
SETTINGS_ITEMS = ("menu.language", "menu.rescan", "menu.back")


class NavigationRenderer(Renderer):
    def page(self, heading, items, focus, *, language="de"):
        from .i18n import Translator
        tr = Translator(language).tr
        t = self.theme
        image = Image.new("RGB", (128, 128), t.background)
        self.shell(image, tr(heading))
        for slot, index in enumerate(self.menu_window(len(items), focus)):
            marker = "<" if items[index] == "menu.back" else ">"
            self.menu_row(image, slot, tr(items[index]), selected=index == focus,
                          marker=marker)
        self.bottom(image, status=tr("status.local_only"),
                    footer=tr("footer.select"))
        return ssd1327_gray(image)

    def network_status(self, snapshot, *, language="de"):
        from .i18n import Translator
        tr = Translator(language).tr
        t = self.theme
        image = Image.new("RGB", (128, 128), t.background)
        self.shell(image, tr("network.status"))
        fields = (
            ("network.ethernet", "network.state." + snapshot.ethernet),
            ("network.wifi", "network.state." + snapshot.wifi),
            ("network.preferred", "network.preferred." + snapshot.preferred),
        )
        for index, (label, value) in enumerate(fields):
            y = 23 + index * 25
            self.text(image, (6, y), tr(label), fill=t.secondary, size=9,
                      width=116, height=11)
            self.text(image, (9, y + 11), tr(value), fill=t.primary, size=10,
                      bold=True, width=113, height=13)
        self.bottom(image, status=tr("network.readonly"),
                    footer=tr("footer.select"))
        return ssd1327_gray(image)
