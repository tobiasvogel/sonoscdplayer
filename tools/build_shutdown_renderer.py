#!/usr/bin/env python3
"""Build-only frame generation for the selected shutdown display backend.

The late-shutdown executable does not need Python, Pillow, or filesystem images.
Each frame combines the icon from the artwork PNG (upper part) with the
localized status text, rendered here at build time in the UI language from
ui.toml. Changing the language therefore requires rebuilding the shutdown
programs. Layout and font live in src/system_screen.py (shared with the OLED
start screen); the C programs need neither Python nor the font.
"""
import argparse
import re
from pathlib import Path
import sys
from PIL import Image, ImageOps, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import system_screen  # noqa: E402
from system_screen import (  # noqa: E402,F401  (re-exported for tests)
    ICON_BOTTOM, TEXT_FONT, TEXT_MARGIN, layout_text)

FRAME_NAMES = ('wait', 'transition', 'reboot', 'final', 'safe')


def _pack(image: Image.Image) -> bytes:
    data = image.tobytes()
    return bytes((((data[i] * 15 + 127) // 255) << 4) |
                 ((data[i + 1] * 15 + 127) // 255)
                 for i in range(0, len(data), 2))


def compose_frame(artwork: Path, text: str) -> bytes:
    """Icon from the artwork PNG plus centered localized text, packed."""
    return _pack(system_screen.compose_image(artwork, text))


def frame_text(name: str, language: str) -> str:
    return system_screen.screen_text(name, language)


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


def c_identifier(language: str) -> str:
    return re.sub(r'[^A-Za-z0-9]', '_', language)


def generate(wait: Path, final: Path, reboot: Path, transition: Path, output: Path, backend: str, safe: Path | None = None, diagnostic: bool = False, language: str | None = None) -> None:
    """Write frames for every installed locale file plus a language table.

    The C programs pick the set at runtime from the UI state file and fall
    back to the default language (ui.toml, or --language). A new language is
    picked up just by adding its locale file; nothing else lists languages.
    """
    if backend != 'ssd1327':
        raise ValueError('unsupported backend: ' + backend)
    from sonos_ui.i18n import available_languages
    languages = available_languages()
    default = language if language is not None else configured_language()
    if default not in languages:
        raise ValueError('default language has no locale file: ' + default)
    sources = {'wait': wait, 'final': final, 'reboot': reboot, 'transition': transition}
    if safe is not None:
        sources['safe'] = safe
    identifiers = {}
    for lang in languages:
        ident = c_identifier(lang)
        if ident in identifiers.values():
            raise ValueError('locale names collide in C: ' + lang)
        identifiers[lang] = ident
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ['/* Generated at build time from the screen artwork and locale files; do not edit. GPL-2.0-only. */',
             '#ifndef SONOSCDPLAYER_SHUTDOWN_FRAMES_H',
             '#define SONOSCDPLAYER_SHUTDOWN_FRAMES_H',
             '#include <stddef.h>',
             '#include <stdint.h>',
             '#define SHUTDOWN_FRAME_WIDTH 128u',
             '#define SHUTDOWN_FRAME_HEIGHT 128u',
             '#define SHUTDOWN_FRAME_BYTES 8192u',
             '#define SHUTDOWN_FRAME_FORMAT "gray4-packed"',
             f'#define SHUTDOWN_DEFAULT_LANGUAGE "{default}"']

    def emit(name, data):
        lines.append(f'static const uint8_t {name}[{len(data)}] = {{')
        for offset in range(0, len(data), 16):
            lines.append('    ' + ', '.join(f'0x{b:02x}' for b in data[offset:offset + 16]) + ',')
        lines.append('};')

    for lang in languages:
        for name, path in sources.items():
            emit(f'shutdown_frame_{name}_{identifiers[lang]}', compose_frame(path, frame_text(name, lang)))
    if diagnostic:
        emit('shutdown_frame_diagnostic', diagnostic_frame())
    lines += ['struct shutdown_frame_set {',
              '    const char *language;',
              '    const uint8_t *wait, *final, *reboot, *transition, *safe;',
              '};',
              'static const struct shutdown_frame_set shutdown_frame_sets[] = {']
    for lang in languages:
        ident = identifiers[lang]
        safe_ref = f'shutdown_frame_safe_{ident}' if safe is not None else 'NULL'
        lines.append(f'    {{"{lang}", shutdown_frame_wait_{ident}, shutdown_frame_final_{ident}, '
                     f'shutdown_frame_reboot_{ident}, shutdown_frame_transition_{ident}, {safe_ref}}},')
    lines += ['};',
              f'#define SHUTDOWN_FRAME_SET_COUNT {len(languages)}u',
              '#endif']
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
                        help='fallback language when no language was chosen in the menu '
                             '(default: [ui] language from ui.toml)')
    args = parser.parse_args()
    generate(args.wait, args.final, args.reboot, args.transition, args.output, args.backend, args.safe, args.diagnostic, args.language)


if __name__ == '__main__':
    main()
