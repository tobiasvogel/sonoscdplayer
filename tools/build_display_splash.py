#!/usr/bin/env python3
"""Generate firmware splash commands for the configured display backend."""
import argparse
from pathlib import Path
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
from sonoscdplayer.hardware.displays.pins import DisplayPins, load_display_pins
from sonoscdplayer.hardware.displays.frames import image_to_frame
from sonoscdplayer.hardware.displays.runtime import backend_for


def frame_bytes(image, config):
    frame = image_to_frame(image, color=backend_for(config.controller).color)
    if config.controller == 'ssd1351':
        from sonoscdplayer.hardware.displays.ssd1351 import DisplayConfig, native_bytes
        return native_bytes(frame, DisplayConfig(rotation=config.rotation,
                                                color_order=config.color_order))
    return bytes(frame.data)


def build(image, config=DisplayPins(), *, chunk=128):
    frame = frame_bytes(image, config)
    if config.controller == 'ssd1327':
        from tools.build_ssd1327_splash import build as legacy_build
        return legacy_build(frame, hz=config.spi_hz, contrast=config.contrast,
                            chunk=chunk, pins=config)
    if type(chunk) is not int or not 1 <= chunk <= 4096:
        raise ValueError('invalid splash chunk size')
    from sonoscdplayer.hardware.displays.ssd1351 import DisplayConfig, initialization_commands
    from sonoscdplayer.hardware.displays.ssd1351_backend import master_current
    native = DisplayConfig(color_order=config.color_order,
                           brightness=master_current(config.contrast))
    lines = [f'# {config.controller} SPI0/CE0; RST{config.rst_pin} must already be HIGH',
             f'define oled spi [copi 10] [cipo 9] [sclk 11] [cs 8] [dc {config.dc_pin}] [freq {config.spi_hz}]']
    # SplashAsm's first byte uses DC LOW, remaining bytes DC HIGH.
    for command, params in initialization_commands(native):
        lines.append('oled ' + ' '.join(f'0x{v:02x}' for v in bytes([command])+params))
    lines += ['oled 0x15 0x00 0x7f', 'oled 0x75 0x00 0x7f', 'oled 0x5c']
    for offset in range(0, len(frame), chunk):
        lines.append('oled [DATA_ONLY] ' + ' '.join(f'0x{v:02x}' for v in frame[offset:offset+chunk]))
    lines.append('oled 0xaf')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('png', nargs='?', type=Path)
    parser.add_argument('--display-config', type=Path)
    parser.add_argument('-o', '--output', type=Path, required=True)
    args = parser.parse_args(argv)
    from sonoscdplayer.system.config_paths import player_config
    config = load_display_pins(args.display_config or player_config())
    if args.png:
        with Image.open(args.png) as source:
            source.load()
            image = source.copy()
    else:
        from sonoscdplayer.ui.system_screens import screen_image
        from sonoscdplayer.ui.config import load_ui_config
        from sonoscdplayer.ui.i18n import available_languages
        from sonoscdplayer.ui.language_state import UiState
        language = (UiState(log=lambda _: None).language(available_languages())
                    or load_ui_config().language)
        image = screen_image('start', language,
                             grayscale=not backend_for(config.controller).color)
    args.output.write_text(build(image, config), encoding='ascii')
    print(f'Wrote {args.output}: {config.controller}')


if __name__ == '__main__':
    main()
