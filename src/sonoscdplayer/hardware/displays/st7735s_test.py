"""Standalone LCD pattern diagnostic; offline by default, no player access."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

from sonoscdplayer.hardware.displays.transport import SPIConfig, SPITransport
from sonoscdplayer.hardware.displays.backlight import BacklightConfig, GPIOBacklight
from sonoscdplayer.hardware.displays.st7735s import DisplayConfig, ST7735S, image_to_frame

PATTERNS = ('colors', 'grid', 'checker', 'text', 'clear')
COLORS = ('red', 'lime', 'blue', 'white', 'cyan', 'magenta', 'yellow', 'black')


def pattern_image(pattern, config=DisplayConfig()):
    """A test pattern (color bars, grid, text, ...) for the configured size."""
    from PIL import Image, ImageDraw, ImageFont
    if pattern not in PATTERNS:
        raise ValueError('unknown test pattern')
    width, height = config.size
    image = Image.new('RGB', config.size, 'black')
    draw = ImageDraw.Draw(image)
    if pattern == 'colors':
        for index, color in enumerate(COLORS):
            draw.rectangle((index*width//8, 0, (index+1)*width//8-1, height-1), fill=color)
    elif pattern == 'grid':
        for x in range(0, width, 16):
            draw.line((x, 0, x, height-1), fill=(80, 80, 80))
        for y in range(0, height, 16):
            draw.line((0, y, width-1, y), fill=(80, 80, 80))
        draw.rectangle((0, 0, width-1, height-1), outline='white')
        draw.rectangle((3, 3, 12, 12), fill='red')
        draw.rectangle((width-13, 3, width-4, 12), fill='lime')
        draw.rectangle((3, height-13, 12, height-4), fill='blue')
        draw.rectangle((width-13, height-13, width-4, height-4), outline='yellow')
    elif pattern == 'checker':
        for y in range(0, height, 8):
            for x in range(0, width, 8):
                if (x//8+y//8) % 2:
                    draw.rectangle((x, y, x+7, y+7), fill='white')
    elif pattern == 'text':
        face = ImageFont.load_default()
        for y, label, color in ((8, 'SONOS CD PLAYER', 'white'),
                                (30, 'ST7735S / SPI', 'cyan'),
                                (52, f'{width} x {height}', 'yellow'),
                                (74, 'RGB565', 'lime'),
                                (96, f'ROT {config.rotation}', 'white')):
            draw.text((6, y), label, font=face, fill=color)
    return image


def main(argv=None):
    """Command line of the ST7735S diagnostic (see docs/ST7735S.md)."""
    parser = argparse.ArgumentParser(description='Waveshare ST7735S SPI pattern test')
    parser.add_argument('--gpio', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--spi-device', type=int, default=0)
    parser.add_argument('--spi-hz', type=int, default=2_000_000)
    # Defaults match the project module as wired and tested.
    parser.add_argument('--dc-pin', type=int, default=22)
    parser.add_argument('--rst-pin', type=int, default=27)
    parser.add_argument('--backlight-pin', type=int, default=None,
                        help='required for hardware; choose a physically available BCM pin')
    parser.add_argument('--spi-miso-released', action='store_true',
                        help='acknowledge no_miso boot configuration for GPIO9; does not change it')
    parser.add_argument('--gpio-warnings', action='store_true')
    parser.add_argument('--rotation', type=int, choices=(0, 90, 180, 270), default=270)
    parser.add_argument('--x-offset', type=int, default=2)
    parser.add_argument('--y-offset', type=int, default=1)
    parser.add_argument('--color-order', choices=('rgb', 'bgr'), default='rgb')
    parser.add_argument('--seconds', type=float, default=3)
    parser.add_argument('--pattern', choices=('all',) + PATTERNS, default='all')
    parser.add_argument('--preview-dir', type=Path, default=None)
    args = parser.parse_args(argv)
    if args.gpio != args.execute:
        parser.error('--gpio and --execute must be given together')
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 60:
        parser.error('--seconds must be finite, >0 and <=60')
    if args.execute and args.backlight_pin is None:
        parser.error('--backlight-pin is required for hardware execution')
    try:
        config = DisplayConfig(args.rotation, args.x_offset, args.y_offset, args.color_order)
        spi_config = SPIConfig(device=args.spi_device, dc_pin=args.dc_pin,
                               rst_pin=args.rst_pin, spi_hz=args.spi_hz)
        bl_config = None
        if args.backlight_pin is not None:
            bl_config = BacklightConfig(args.backlight_pin, args.spi_miso_released)
            bl_config.validate_control_pins(spi_config)
    except ValueError as exc:
        parser.error(str(exc))
    patterns = PATTERNS if args.pattern == 'all' else (args.pattern,)
    frames = []
    for pattern in patterns:
        image = pattern_image(pattern, config)
        frames.append((pattern, image_to_frame(image, config)))
        if args.preview_dir is not None:
            args.preview_dir.mkdir(parents=True, exist_ok=True)
            image.save(args.preview_dir / f'st7735s-{pattern}.png')
    # No transport, backlight or hardware-library construction in offline mode.
    driver = None
    if args.execute:
        driver = ST7735S(SPITransport(spi_config, gpio_warnings=args.gpio_warnings),
                         GPIOBacklight(bl_config, gpio_warnings=args.gpio_warnings), config)
    try:
        if driver:
            driver.open()
        for pattern, frame in frames:
            print(json.dumps(dict(pattern=pattern, width=frame.width, height=frame.height,
                                  bytes=len(frame.data), hardware=args.execute,
                                  backlight_pin=args.backlight_pin,
                                  sha256=hashlib.sha256(frame.data).hexdigest())), flush=True)
            if driver:
                driver.show(frame)
                time.sleep(args.seconds)
    finally:
        if driver:
            driver.close()


if __name__ == '__main__':
    main()
