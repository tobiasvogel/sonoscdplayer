"""Standalone SH1107 diagnostic: offline by default, no player/Sonos access."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

from .display_transport import SPIConfig, SPITransport, I2CConfig, I2CTransport
from .sh1107 import DisplayConfig, SH1107, image_to_frame

PATTERNS = ('grid', 'checker', 'text', 'clear')


def pattern_image(pattern, config=DisplayConfig()):
    from PIL import Image, ImageDraw, ImageFont
    if pattern not in PATTERNS:
        raise ValueError('unknown test pattern')
    width, height = config.size
    image = Image.new('1', config.size, 0)
    draw = ImageDraw.Draw(image)
    if pattern == 'grid':
        for x in range(0, width, 16):
            draw.line((x, 0, x, height - 1), fill=1)
        for y in range(0, height, 16):
            draw.line((0, y, width - 1, y), fill=1)
        draw.rectangle((0, 0, width - 1, height - 1), outline=1)
        # Asymmetric markers distinguish rotation and mirroring.
        draw.rectangle((3, 3, 10, 10), fill=1)
        draw.rectangle((width - 11, height - 11, width - 4, height - 4), outline=1)
    elif pattern == 'checker':
        for y in range(height):
            for x in range(width):
                if (x // 8 + y // 8) % 2:
                    draw.point((x, y), fill=1)
    elif pattern == 'text':
        font = ImageFont.load_default()
        for y, label in zip((2, 17, 32, 47),
                            ('SONOS CD', 'SH1107', f'{width}x{height}', 'BUS TEST')):
            draw.text((2, y), label, font=font, fill=1)
    return image


def main(argv=None):
    parser = argparse.ArgumentParser(description='Standalone Waveshare SH1107 SPI/I2C test')
    parser.add_argument('--gpio', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--interface', choices=('spi', 'i2c'), default='spi')
    parser.add_argument('--spi-device', type=int, default=None)
    parser.add_argument('--spi-hz', type=int, default=None)
    parser.add_argument('--dc-pin', type=int, default=None)
    parser.add_argument('--i2c-bus', type=int, default=None)
    parser.add_argument('--i2c-address', type=lambda v: int(v, 0), default=None,
                        help='7-bit address: 0x3C (DC low) or 0x3D (DC high)')
    # Defaults match the project module as wired and tested (18.11.).
    parser.add_argument('--rst-pin', type=int, default=27)
    parser.add_argument('--gpio-warnings', action='store_true')
    parser.add_argument('--rotation', type=int, choices=(0, 90, 180, 270), default=270)
    parser.add_argument('--display-offset', type=lambda v: int(v, 0), default=0x60)
    parser.add_argument('--contrast', type=int, default=128)
    parser.add_argument('--seconds', type=float, default=3)
    parser.add_argument('--pattern', choices=('all',) + PATTERNS, default='all')
    parser.add_argument('--preview-dir', type=Path, default=None)
    args = parser.parse_args(argv)
    if args.gpio != args.execute:
        parser.error('--gpio and --execute must be given together')
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 60:
        parser.error('--seconds must be finite, >0 and <=60')
    try:
        config = DisplayConfig(args.rotation, args.contrast, args.display_offset)
        if args.interface == 'spi':
            if args.i2c_bus is not None or args.i2c_address is not None:
                parser.error('--i2c-bus/--i2c-address require --interface i2c')
            bus_config = SPIConfig(
                device=0 if args.spi_device is None else args.spi_device,
                dc_pin=22 if args.dc_pin is None else args.dc_pin,
                rst_pin=args.rst_pin,
                spi_hz=2_000_000 if args.spi_hz is None else args.spi_hz)
            transport_class = SPITransport
        else:
            if any(value is not None for value in (args.spi_device, args.spi_hz, args.dc_pin)):
                parser.error('--spi-device/--spi-hz/--dc-pin require --interface spi')
            bus_config = I2CConfig(
                bus=1 if args.i2c_bus is None else args.i2c_bus,
                address=0x3C if args.i2c_address is None else args.i2c_address,
                rst_pin=args.rst_pin)
            transport_class = I2CTransport
    except ValueError as exc:
        parser.error(str(exc))
    patterns = PATTERNS if args.pattern == 'all' else (args.pattern,)
    # Build everything before opening hardware, including dependency checks.
    frames = []
    for pattern in patterns:
        image = pattern_image(pattern, config)
        frames.append((pattern, image_to_frame(image, config)))
        if args.preview_dir is not None:
            args.preview_dir.mkdir(parents=True, exist_ok=True)
            image.save(args.preview_dir / f'sh1107-{pattern}.png')
    driver = (SH1107(transport_class(bus_config, gpio_warnings=args.gpio_warnings), config)
              if args.execute else None)
    try:
        if driver:
            driver.open()
        for pattern, frame in frames:
            print(json.dumps(dict(pattern=pattern, width=frame.width, height=frame.height,
                                  interface=args.interface,
                                  bytes=len(frame.data), hardware=args.execute,
                                  sha256=hashlib.sha256(frame.data).hexdigest())), flush=True)
            if driver:
                driver.show(frame)
                time.sleep(args.seconds)
    finally:
        if driver:
            driver.close()


if __name__ == '__main__':
    main()
