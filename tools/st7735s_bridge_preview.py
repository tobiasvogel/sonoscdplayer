#!/usr/bin/env python3
"""Offline ST7735S bridge smoke test: write previews, never touch the LCD.

Run from the repository root with --output-dir. These are synthetic samples,
not data fetched from the running player or Sonos.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from sonoscdplayer.ui.displays.st7735s_bridge import ST7735SFrameBridge  # noqa: E402
from sonoscdplayer.ui.renderers.landscape_160x128 import LandscapeRenderer  # noqa: E402
from sonoscdplayer.ui.screens.semantic_screen import ScreenItem, SemanticScreen  # noqa: E402


def samples():
    """Local, non-sensitive screen fixtures; never issue player commands."""
    return (
        ('playback', SemanticScreen('playback', heading='playback', items=(
            ScreenItem('track', 'track', value='3/12'),
            ScreenItem('playback', 'playback', value='playing'),
            ScreenItem('album', 'album', value='Sample album'),
            ScreenItem('track_title', 'track', value='Sample title'),
            ScreenItem('artist', 'artist', value='Sample artist'),
        ))),
        ('menu', SemanticScreen('menu', heading='Speakers', items=(
            ScreenItem('room1', 'Living room', focused=True, selected=True),
            ScreenItem('room2', 'Kitchen'),
            ScreenItem('back', 'Back', intent='back'),
        ))),
        ('notice', SemanticScreen('notice', heading='Information',
                                  message='Display preview only', severity='info')),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path,
                        help='where to write three PNG previews (no hardware access)')
    args = parser.parse_args(argv)
    # No controller/backlight/transport is created. The conversion path is
    # checked for every sample before we write any images.
    bridge = ST7735SFrameBridge()
    renderer = LandscapeRenderer()
    rendered = []
    for name, screen in samples():
        frame = bridge.from_screen(screen)
        if (frame.width, frame.height, frame.pixel_format, len(frame.data)) != (
                160, 128, 'rgb565', 40960):
            raise ValueError('unexpected ST7735S frame contract')
        rendered.append((name, renderer.render(screen)))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, image in rendered:
        destination = args.output_dir / f'st7735s-{name}.png'
        image.save(destination)
        print(destination)
    print('Offline preview only. No display switching, GPIO or SPI access.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
