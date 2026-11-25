#!/usr/bin/env python3
"""Build packaged boot/shutdown artwork from the original colored logo SVG.

Developer-only rasterization: normal screen deployment uses the committed PNGs
and needs only Pillow. Regeneration uses CairoSVG, or --rasterized-logo with a
transparent square PNG exported from this SVG by another vector renderer.
"""
import argparse
import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCREENS = ROOT/'src/sonoscdplayer/assets/screens'
LOGO_SCREENS = ('start_screen.png', 'shutdown_screen.png', 'reboot_screen.png',
                'restart_screen.png', 'pleasewait_screen.png')
LOGO_SIZE = 64
LOGO_POSITION = (32, 4)
ICON_BOTTOM = 72


def build_screens(directory=SCREENS, *, rasterized_logo=None):
    directory = Path(directory)
    if rasterized_logo is None:
        try:
            import cairosvg
        except ImportError as exc:
            raise RuntimeError('Regeneration needs CairoSVG or --rasterized-logo; '
                               'normal deployment uses the packaged PNGs') from exc
        payload = cairosvg.svg2png(url=str(directory/'logo.svg'),
                                  output_width=1040, output_height=1040)
        source = Image.open(io.BytesIO(payload))
    else:
        source = Image.open(rasterized_logo)
    with source:
        if source.width != source.height or not 128 <= source.width <= 4096:
            raise ValueError('logo export must be square, 128..4096 pixels')
        logo = source.convert('RGBA').resize((LOGO_SIZE, LOGO_SIZE),
                                            Image.Resampling.LANCZOS)
    for name in LOGO_SCREENS:
        path = directory/name
        with Image.open(path) as original:
            if original.size != (128, 128):
                raise ValueError('system artwork must be 128x128: '+name)
            image = original.convert('RGBA')
        # Preserve the legacy fallback text; localized rendering replaces it.
        image.paste((0, 0, 0, 255), (0, 0, 128, ICON_BOTTOM))
        image.alpha_composite(logo, LOGO_POSITION)
        image.convert('RGB').save(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rasterized-logo', type=Path)
    args = parser.parse_args(argv)
    build_screens(rasterized_logo=args.rasterized_logo)


if __name__ == '__main__':
    main()
