"""Localized full-screen system messages (start, shutdown, restart).

Each screen combines the icon from an artwork PNG in ``src/data`` (upper part)
with a localized status text, centered with side margins in the bundled font
Encode Sans Semi Condensed (SIL OFL 1.1, ``src/fonts``).

Used at runtime for the OLED start screen and at build time for the shutdown
frames of the C programs (``tools/build_shutdown_renderer.py``).
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

SRC = Path(__file__).resolve().parent
DATA_DIR = SRC / 'data'
TEXT_FONT = SRC / 'fonts' / 'EncodeSansSemiCondensed-Medium.ttf'

SCREEN = 128
ICON_BOTTOM = 72        # artwork rows above this hold the icon, below the old text
TEXT_TOP = 76
TEXT_BOTTOM = 122
TEXT_MARGIN = 8         # free columns left and right of every text line
TEXT_SIZES = (12, 11, 10)
LINE_GAP = 3
MAX_LINES = 2

# Screen name -> (artwork PNG, translation key, append progress ellipsis)
SCREENS = {
    'start': ('start_screen.png', 'system.starting', True),
    'wait': ('shutdown_screen.png', 'system.shutting_down', True),
    'transition': ('shutdown_screen.png', 'system.shutting_down', True),
    'reboot': ('reboot_screen.png', 'system.restarting', True),
    'final': ('pleasewait_screen.png', 'system.please_wait', False),
    'safe': ('safetounplug_screen.png', 'system.safe_to_unplug', False),
}


def load_artwork(path):
    with Image.open(path) as source:
        source.load()
        if not 0 < source.width <= 4096 or not 0 < source.height <= 4096:
            raise ValueError('invalid source image dimensions')
        rgba = source.convert('RGBA')
    backdrop = Image.new('RGBA', rgba.size, (0, 0, 0, 255))
    mono = Image.alpha_composite(backdrop, rgba).convert('L')
    fitted = ImageOps.contain(mono, (SCREEN, SCREEN), method=Image.Resampling.LANCZOS)
    canvas = Image.new('L', (SCREEN, SCREEN), 0)
    canvas.paste(fitted, ((SCREEN - fitted.width) // 2, (SCREEN - fitted.height) // 2))
    return canvas


def _wrap(text, fnt, width):
    lines, current = [], ''
    for word in text.split():
        candidate = word if not current else current + ' ' + word
        if fnt.getlength(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def layout_text(text):
    """Return (font, lines) that fit the text area with side margins."""
    width = SCREEN - 2 * TEXT_MARGIN
    for size in TEXT_SIZES:
        fnt = ImageFont.truetype(str(TEXT_FONT), size)
        lines = _wrap(text, fnt, width)
        if len(lines) > MAX_LINES:
            continue
        if any(fnt.getbbox(line)[2] - fnt.getbbox(line)[0] > width for line in lines):
            continue
        return fnt, lines
    raise ValueError('system screen text does not fit the display: ' + text)


def compose_image(artwork, text):
    """Grayscale 128x128 image: artwork icon plus centered text."""
    image = load_artwork(artwork)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, ICON_BOTTOM, SCREEN - 1, SCREEN - 1), fill=0)
    fnt, lines = layout_text(text)
    ascent, descent = fnt.getmetrics()
    line_height = ascent + descent
    block = len(lines) * line_height + (len(lines) - 1) * LINE_GAP
    y = TEXT_TOP + max(0, (TEXT_BOTTOM - TEXT_TOP - block) // 2)
    for line in lines:
        left, _, right, _ = draw.textbbox((0, 0), line, font=fnt)
        x = (SCREEN - (right - left)) // 2 - left
        draw.text((x, y), line, font=fnt, fill=255)
        y += line_height + LINE_GAP
    return image


def screen_text(name, language):
    from sonos_ui.i18n import Translator
    _, key, progress = SCREENS[name]
    text = Translator(language).tr(key)
    return text + '…' if progress else text


def screen_image(name, language, artwork=None):
    """Localized system screen; artwork defaults to the standard PNG."""
    if artwork is None:
        artwork = DATA_DIR / SCREENS[name][0]
    return compose_image(artwork, screen_text(name, language))
