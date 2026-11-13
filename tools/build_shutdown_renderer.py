#!/usr/bin/env python3
"""Build-only frame generation for the selected shutdown display backend.

The late-shutdown executable does not need Python, Pillow, or filesystem images.
Each frame combines the icon from the artwork PNG (upper part) with the
localized status text, rendered here at build time in the UI language from
ui.toml. Changing the language therefore requires rebuilding the shutdown
programs. The text font (Encode Sans Semi Condensed, SIL OFL 1.1) ships in
src/fonts and is only needed at build time.
"""
import argparse
from pathlib import Path
import sys
from PIL import Image, ImageOps, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
TEXT_FONT = ROOT / 'src' / 'fonts' / 'EncodeSansSemiCondensed-Medium.ttf'

SCREEN = 128
ICON_BOTTOM = 72        # artwork rows above this hold the icon, below the old text
TEXT_TOP = 76
TEXT_BOTTOM = 122
TEXT_MARGIN = 8         # free columns left and right of every text line
TEXT_SIZES = (12, 11, 10)
LINE_GAP = 3
MAX_LINES = 2

# Frame name -> (translation key, append progress ellipsis)
FRAME_TEXT = {
    'wait': ('system.shutting_down', True),
    'transition': ('system.shutting_down', True),
    'reboot': ('system.restarting', True),
    'final': ('system.please_wait', False),
    'safe': ('system.safe_to_unplug', False),
}


def _pack(image: Image.Image) -> bytes:
    data = image.tobytes()
    return bytes((((data[i] * 15 + 127) // 255) << 4) |
                 ((data[i + 1] * 15 + 127) // 255)
                 for i in range(0, len(data), 2))


def _load_artwork(path: Path) -> Image.Image:
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
    raise ValueError('shutdown text does not fit the display: ' + text)


def compose_frame(artwork: Path, text: str) -> bytes:
    """Icon from the artwork PNG plus centered localized text, packed."""
    image = _load_artwork(artwork)
    ImageDraw.Draw(image).rectangle((0, ICON_BOTTOM, SCREEN - 1, SCREEN - 1), fill=0)
    fnt, lines = layout_text(text)
    draw = ImageDraw.Draw(image)
    ascent, descent = fnt.getmetrics()
    line_height = ascent + descent
    block = len(lines) * line_height + (len(lines) - 1) * LINE_GAP
    y = TEXT_TOP + max(0, (TEXT_BOTTOM - TEXT_TOP - block) // 2)
    for line in lines:
        left, _, right, _ = draw.textbbox((0, 0), line, font=fnt)
        x = (SCREEN - (right - left)) // 2 - left
        draw.text((x, y), line, font=fnt, fill=255)
        y += line_height + LINE_GAP
    return _pack(image)


def frame_text(name: str, language: str) -> str:
    from sonos_ui.i18n import Translator
    key, progress = FRAME_TEXT[name]
    text = Translator(language).tr(key)
    return text + '\u2026' if progress else text


def configured_language() -> str:
    from sonos_ui.ui_config import load_ui_config
    return load_ui_config().language


def pack_ssd1327(path: Path) -> bytes:
    with Image.open(path) as source:
        source.load()
        if not 0 < source.width <= 4096 or not 0 < source.height <= 4096:
            raise ValueError('invalid source image dimensions')
        rgba = source.convert('RGBA')
    backdrop = Image.new('RGBA', rgba.size, (0, 0, 0, 255))
    mono = Image.alpha_composite(backdrop, rgba).convert('L')
    fitted = ImageOps.contain(mono, (128, 128), method=Image.Resampling.LANCZOS)
    canvas = Image.new('L', (128, 128), 0)
    canvas.paste(fitted, ((128 - fitted.width) // 2, (128 - fitted.height) // 2))
    return _pack(canvas)


def diagnostic_frame() -> bytes:
    """Unmistakable non-safety live exitrd proof: patterned TEST display."""
    image = Image.new('L', (128, 128), 0)
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 125, 125), outline=255, width=4)
    for y in (14, 106):
        draw.rectangle((12, y, 115, y + 5), fill=150)
    # Pillow's built-in bitmap font avoids system font files in the build.
    font = ImageFont.load_default()
    for line, y in (('EXITRD', 30), ('TEST', 54), ('OK', 78)):
        box = draw.textbbox((0, 0), line, font=font)
        tw = box[2] - box[0]
        draw.text(((128 - tw) // 2, y), line, font=font, fill=255,
                  stroke_width=0)
    pixels = image.tobytes()
    return bytes((((pixels[i] * 15 + 127) // 255) << 4) |
                 ((pixels[i + 1] * 15 + 127) // 255)
                 for i in range(0, len(pixels), 2))


def generate(wait: Path, final: Path, reboot: Path, transition: Path, output: Path, backend: str, safe: Path | None = None, diagnostic: bool = False, language: str | None = None) -> None:
    if backend != 'ssd1327':
        raise ValueError('unsupported backend: ' + backend)
    if language is None:
        language = configured_language()
    sources = {'wait': wait, 'final': final, 'reboot': reboot, 'transition': transition}
    if safe is not None:
        sources['safe'] = safe
    frames = {name: compose_frame(path, frame_text(name, language))
              for name, path in sources.items()}
    if diagnostic:
        frames['diagnostic'] = diagnostic_frame()
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ['/* Generated at build time; do not edit. GPL-2.0-only. */',
             '#ifndef SONOSCDPLAYER_SHUTDOWN_FRAMES_H',
             '#define SONOSCDPLAYER_SHUTDOWN_FRAMES_H',
             '#include <stdint.h>',
             '#define SHUTDOWN_FRAME_WIDTH 128u',
             '#define SHUTDOWN_FRAME_HEIGHT 128u',
             '#define SHUTDOWN_FRAME_FORMAT "gray4-packed"']
    for name, data in frames.items():
        lines.append(f'static const uint8_t shutdown_frame_{name}[{len(data)}] = {{')
        for offset in range(0, len(data), 16):
            lines.append('    ' + ', '.join(f'0x{b:02x}' for b in data[offset:offset + 16]) + ',')
        lines.append('};')
    lines.append('#endif')
    output.write_text('\n'.join(lines) + '\n', encoding='ascii')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', choices=['ssd1327'], default='ssd1327')
    parser.add_argument('--wait', type=Path, required=True)
    parser.add_argument('--final', type=Path, required=True)
    parser.add_argument('--reboot', type=Path, required=True)
    parser.add_argument('--transition', type=Path, required=True)
    parser.add_argument('--safe', type=Path, default=None)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--diagnostic', action='store_true', help='embed TEST frame in opt-in exitrd build')
    parser.add_argument('--language', default=None,
                        help='UI language for the frame text (default: [ui] language from ui.toml)')
    args = parser.parse_args()
    generate(args.wait, args.final, args.reboot, args.transition, args.output, args.backend, args.safe, args.diagnostic, args.language)


if __name__ == '__main__':
    main()
