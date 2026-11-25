#!/usr/bin/env python3
"""Resolve build-time display selection from the same TOML as the live service."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sonoscdplayer.hardware.displays.pins import load_display_pins
from sonoscdplayer.hardware.displays.runtime import backend_for


def render_header(config):
    return ('/* Generated from [display]; do not edit. */\n'
            '#ifndef SONOSCDPLAYER_DISPLAY_CONFIG_H\n'
            '#define SONOSCDPLAYER_DISPLAY_CONFIG_H\n'
            f'#define SONOSCDPLAYER_OLED_SPI_BUS {config.spi_bus}u\n'
            f'#define SONOSCDPLAYER_OLED_SPI_DEVICE {config.spi_device}u\n'
            f'#define SONOSCDPLAYER_OLED_DC_PIN {config.dc_pin}u\n'
            f'#define SONOSCDPLAYER_OLED_RST_PIN {config.rst_pin}u\n'
            f'#define SONOSCDPLAYER_DISPLAY_SPI_HZ {config.spi_hz}u\n'
            f'#define SONOSCDPLAYER_DISPLAY_CONTROLLER "{config.controller}"\n'
            '#endif\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    config = load_display_pins(args.config)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_header(config), encoding='ascii')
    print(backend_for(config.controller).controller)


if __name__ == '__main__':
    main()
