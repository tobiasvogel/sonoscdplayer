"""Standalone SSD1351 diagnostic: no player, input or service interaction."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from sonoscdplayer.hardware.displays.transport import SPIConfig, SPITransport
from sonoscdplayer.hardware.displays.ssd1351 import DisplayConfig, SSD1351, image_to_frame

PATTERNS = ('colors', 'grid', 'checker', 'text', 'brightness', 'clear')
BRIGHTNESS_STEPS = (1, 5, 10, 15)


def pattern_image(pattern, config=DisplayConfig()):
    from PIL import Image, ImageDraw, ImageFont
    if pattern not in PATTERNS:
        raise ValueError('unknown SSD1351 pattern')
    image = Image.new('RGB', config.size, 'black')
    draw = ImageDraw.Draw(image)
    face = ImageFont.load_default()
    if pattern == 'colors':
        for index, color in enumerate(('red', 'lime', 'blue', 'white', 'cyan', 'magenta', 'yellow', 'black')):
            draw.rectangle((index*16, 0, index*16+15, 127), fill=color)
    elif pattern == 'grid':
        for at in range(0, 128, 16):
            draw.line((at, 0, at, 127), fill=(80, 80, 80))
            draw.line((0, at, 127, at), fill=(80, 80, 80))
        draw.rectangle((0, 0, 127, 127), outline='white')
        draw.rectangle((3, 3, 12, 12), fill='red')
        draw.rectangle((115, 3, 124, 12), fill='lime')
        draw.rectangle((3, 115, 12, 124), fill='blue')
        draw.rectangle((115, 115, 124, 124), outline='yellow')
    elif pattern == 'checker':
        for y in range(0, 128, 8):
            for x in range(0, 128, 8):
                if (x//8+y//8) % 2:
                    draw.rectangle((x, y, x+7, y+7), fill='white')
    elif pattern == 'text':
        for y, label, color in ((8, 'SONOS CD', 'white'), (29, 'SSD1351 / SPI', 'cyan'),
                                 (50, '128 x 128 RGB', 'yellow'), (71, 'RGB565', 'lime'),
                                 (92, f'ROT {config.rotation}', 'white')):
            draw.text((6, y), label, font=face, fill=color)
    elif pattern == 'brightness':
        draw.text((6, 8), 'MASTER CURRENT', font=face, fill='white')
        for y, color in ((32, 'red'), (54, 'lime'), (76, 'blue'), (98, 'white')):
            draw.rectangle((8, y, 119, y+13), fill=color)
    return image


def pattern_steps(pattern, brightness):
    names = PATTERNS if pattern == 'all' else (pattern,)
    return [(name, level) for name in names
            for level in (BRIGHTNESS_STEPS if name == 'brightness' else (brightness,))]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Waveshare SSD1351 RGB OLED hardware test')
    parser.add_argument('--gpio', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--spi-device', type=int, default=0)
    parser.add_argument('--spi-hz', type=int, default=2_000_000)
    parser.add_argument('--dc-pin', type=int, default=25)
    parser.add_argument('--rst-pin', type=int, default=24)
    parser.add_argument('--gpio-warnings', action='store_true')
    parser.add_argument('--rotation', type=int, choices=(0, 90, 180, 270), default=0)
    parser.add_argument('--color-order', choices=('rgb', 'bgr'), default='rgb')
    parser.add_argument('--brightness', type=int, default=8, help='C7 master current 0..15; 0 is not OFF')
    parser.add_argument('--seconds', type=float, default=3, help='duration per pattern/brightness step')
    scenes = parser.add_mutually_exclusive_group()
    scenes.add_argument('--pattern', choices=('all',)+PATTERNS, default='all')
    scenes.add_argument('--demo', choices=('overview', 'menu', 'all'),
                        help='synthetic RGB UI preview; no live player or input binding')
    parser.add_argument('--preview-dir', type=Path, default=None)
    args = parser.parse_args(argv)
    if args.gpio != args.execute:
        parser.error('--gpio and --execute must be given together')
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 60:
        parser.error('--seconds must be finite, >0 and <=60')
    try:
        config = DisplayConfig(args.rotation, args.color_order, args.brightness)
        spi_config = SPIConfig(device=args.spi_device, dc_pin=args.dc_pin,
                               rst_pin=args.rst_pin, spi_hz=args.spi_hz)
    except ValueError as exc:
        parser.error(str(exc))
    if args.demo:
        # Render before opening hardware, including the required Unicode fonts.
        from sonoscdplayer.hardware.displays.ssd1351_preview import demo_images
        images = demo_images(args.demo)
        steps = [(name, args.brightness) for name, _ in images]
    else:
        names = PATTERNS if args.pattern == 'all' else (args.pattern,)
        images = [(name, pattern_image(name, config)) for name in names]
        steps = pattern_steps(args.pattern, args.brightness)
    frames = {}
    for name, image in images:
        frames[name] = image_to_frame(image, config)
        if args.preview_dir is not None:
            args.preview_dir.mkdir(parents=True, exist_ok=True)
            image.save(args.preview_dir / f'ssd1351-{name}.png')
    driver = None
    if args.execute:
        driver = SSD1351(SPITransport(spi_config, gpio_warnings=args.gpio_warnings), config)
    try:
        if driver:
            driver.open()
        for name, brightness in steps:
            frame = frames[name]
            print(json.dumps(dict(pattern=name, brightness=brightness, rotation=args.rotation,
                                  color_order=args.color_order, width=128, height=128,
                                  bytes=len(frame.data), hardware=args.execute,
                                  sha256=hashlib.sha256(frame.data).hexdigest())), flush=True)
            if driver:
                driver.set_brightness(brightness)
                driver.show(frame)
                time.sleep(args.seconds)
    finally:
        pending_error = sys.exc_info()[0] is not None
        if driver:
            try:
                driver.close()
            except Exception as exc:
                if not pending_error:
                    raise
                print(f'SSD1351 cleanup also failed: {exc}', file=sys.stderr)


if __name__ == '__main__':
    main()
