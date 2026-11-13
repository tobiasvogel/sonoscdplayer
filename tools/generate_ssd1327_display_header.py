#!/usr/bin/env python3
"""Generate the untracked SSD1327 shutdown C pin header from [display] TOML."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.integrations.outputs.display_pins import load_display_pins


def render_header(pins):
    return (
        "/* Generated from local [display] configuration. Do not edit. */\n"
        "#ifndef SONOSCDPLAYER_SSD1327_DISPLAY_CONFIG_H\n"
        "#define SONOSCDPLAYER_SSD1327_DISPLAY_CONFIG_H\n"
        f"#define SONOSCDPLAYER_OLED_SPI_BUS {pins.spi_bus}u\n"
        f"#define SONOSCDPLAYER_OLED_SPI_DEVICE {pins.spi_device}u\n"
        f"#define SONOSCDPLAYER_OLED_DC_PIN {pins.dc_pin}u\n"
        f"#define SONOSCDPLAYER_OLED_RST_PIN {pins.rst_pin}u\n"
        "#endif\n"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True,
                        help='Local TOML containing [display] pin mapping')
    parser.add_argument('--output', type=Path, required=True,
                        help='Output header in the shutdown build directory')
    args = parser.parse_args(argv)
    pins = load_display_pins(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_header(pins), encoding='ascii')
    print(f'Generated {args.output}')


if __name__ == '__main__':
    main()
