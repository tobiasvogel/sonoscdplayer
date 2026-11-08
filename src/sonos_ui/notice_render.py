"""Offscreen notices using existing UI themes and packaged icon assets.

Rendering is intentionally read-only. The notice scheduler and UI navigation
will be implemented separately in UI-NOTICE-02.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps
from .notice import Notice, notice_text
from .render import font
from .theme import MODERN_HIFI, ssd1327_gray

ICON_ROOT = Path(__file__).resolve().parents[1] / "data" / "icons"
ICON_PREFIX = {"info": "info_ ", "success": "success_",
               "warning": "warning_", "error": "error_"}
COLOR = {"info": "secondary", "success": "playing",
         "warning": "warning", "error": "warning"}


def icon_file(severity, grayscale=False, icon_root=ICON_ROOT):
    """Use exactly the published filenames, including the legacy info space."""
    prefix = ICON_PREFIX[severity]
    suffix = "bw_small" if grayscale else "color_small"
    path = Path(icon_root) / (prefix + suffix + ".png")
    return path if path.is_file() else None


class NoticeRenderer:
    def __init__(self, theme=MODERN_HIFI, icon_root=ICON_ROOT):
        self.theme = theme
        self.icon_root = Path(icon_root)

    def render(self, notice: Notice, *, language="de", size=(128, 128),
               grayscale=False):
        width, height = size
        if not (isinstance(width, int) and isinstance(height, int)
                and width >= 64 and height >= 64):
            raise ValueError("notice display must be at least 64x64")
        message = notice_text(notice, language)
        image = Image.new("RGB", (width, height), self.theme.background)
        draw = ImageDraw.Draw(image)
        tone = getattr(self.theme, COLOR[notice.severity])
        margin = max(4, width // 24)
        icon_size = min(40, max(16, height // 3), width // 3)
        icon_path = icon_file(notice.severity, grayscale, self.icon_root)
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
