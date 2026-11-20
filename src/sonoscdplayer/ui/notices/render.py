"""Offscreen notices using existing UI themes and packaged icon assets.

Rendering is intentionally read-only. The notice scheduler and UI navigation
is handled by the notice engine (notices/engine.py).
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps
from sonoscdplayer.system.package_paths import ICONS
from sonoscdplayer.ui.notices.notice import Notice, notice_text
from sonoscdplayer.ui.renderers.oled_128 import font
from sonoscdplayer.ui.renderers.theme import MODERN_HIFI, ssd1327_gray

ICON_ROOT = ICONS
ICON_PREFIX = {"info": "info_", "success": "success_",
               "warning": "warning_", "error": "error_"}
COLOR = {"info": "secondary", "success": "playing",
         "warning": "warning", "error": "warning"}


def icon_file(severity, grayscale=False, icon_root=ICON_ROOT):
    """Prefer normalized filenames, with compatibility for legacy info icons."""
    prefix = ICON_PREFIX[severity]
    suffix = "bw_small" if grayscale else "color_small"
    root = Path(icon_root)
    path = root / (prefix + suffix + ".png")
    if path.is_file():
        return path
    return None


class NoticeRenderer:
    """Draws a notice with its icon on the 128x128 OLED."""
    def __init__(self, theme=MODERN_HIFI, icon_root=ICON_ROOT):
        self.theme = theme
        self.icon_root = Path(icon_root)

    def render(self, notice: Notice, *, language="de", size=(128, 128),
               grayscale=False, dismiss_hint=False):
        """dismiss_hint adds a bottom line telling how to close a notice
        that does not disappear by itself."""
        hint = ""
        if dismiss_hint:
            from sonoscdplayer.ui.i18n import Translator
            hint = Translator(language).tr("notice.dismiss_hint")
        return self.render_text(notice_text(notice, language), notice.severity,
                                size=size, grayscale=grayscale, hint=hint)

    def render_text(self, message, severity, *, size=(128, 128),
                    grayscale=False, hint=""):
        """Draw an already translated notice; ``hint`` is the optional bottom line."""
        width, height = size
        if not (isinstance(width, int) and isinstance(height, int)
                and width >= 64 and height >= 64):
            raise ValueError("notice display must be at least 64x64")
        image = Image.new("RGB", (width, height), self.theme.background)
        draw = ImageDraw.Draw(image)
        tone = getattr(self.theme, COLOR[severity])
        margin = max(4, width // 24)
        icon_size = min(40, max(16, height // 3), width // 3)
        icon_path = icon_file(severity, grayscale, self.icon_root)
        if icon_path is not None:
            with Image.open(icon_path) as source:
                artwork = ImageOps.contain(source.convert("RGBA"),
                                           (icon_size, icon_size))
            image.paste(artwork, ((width - artwork.width) // 2, margin),
                        artwork)
        else:
            draw.ellipse((width // 2 - 5, margin + 5,
                          width // 2 + 5, margin + 15), outline=tone, width=2)

        top = margin + icon_size + 7
        bottom = height - margin
        if hint:
            hint_font = font(8)
            box = draw.textbbox((0, 0), hint, font=hint_font)
            hint_top = height - margin - (box[3] - box[1])
            draw.line((margin, hint_top - 4, width - margin, hint_top - 4),
                      fill=self.theme.muted)
            draw.text(((width - (box[2] - box[0])) // 2 - box[0], hint_top - box[1]),
                      hint, fill=self.theme.secondary, font=hint_font)
            bottom = hint_top - 7
        available_width = width - 2 * margin
        text_font = font(max(8, min(12, width // 12)))
        lines = []
        for paragraph in message.splitlines():
            current = ""
            for word in paragraph.split():
                candidate = (current + " " + word).strip()
                if draw.textbbox((0, 0), candidate, font=text_font)[2] <= available_width:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    # Long unbroken tokens are clipped by the dedicated viewport.
                    current = word
            lines.append(current)
        line_height = max(11, text_font.size + 3)
        visible = max(1, (bottom - top) // line_height)
        layer = Image.new("RGBA", (available_width, bottom - top), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        for i, line in enumerate(lines[:visible]):
            box = pen.textbbox((0, 0), line, font=text_font)
            x = max(0, (available_width - (box[2] - box[0])) // 2)
            pen.text((x - box[0], i * line_height - box[1]), line,
                     fill=self.theme.primary, font=text_font)
        image.paste(layer, (margin, top), layer)
        return ssd1327_gray(image) if grayscale else image
