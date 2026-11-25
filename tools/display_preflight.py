#!/usr/bin/env python3
"""Read-only display change preflight. Never probes hardware or authorizes switching.

The live display service implements SSD1327 and SSD1351. Other candidates are
informational, even if their standalone test-pattern drivers work on hardware.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import tomllib

CANDIDATES = {
    'ssd1351': ('128x128', 'live RGB OLED runtime', 0),
    'ssd1327': ('128x128', 'current live player runtime', None),
    'st7735s': ('160x128', 'standalone driver only', 270),
    'sh1107': ('128x64', 'standalone driver only', 270),
}
SPI0 = {7, 8, 9, 10, 11}


def read_interface(path: Path):
    """Return parsed document or None plus diagnostic; never assume example pins."""
    try:
        with path.open('rb') as stream:
            return tomllib.load(stream), None
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        return None, f'UNKNOWN: cannot inspect {path}: {exc}'


def _mapped_pins(player: dict, interface: dict | None):
    """Collect explicit inputs and LEDs; missing groups remain unknown."""
    pins = {}
    unknown = []

    def append(pin, label):
        if type(pin) is not int or not 0 <= pin <= 27:
            unknown.append(f'invalid BCM value for {label}: {pin!r}')
        else:
            pins.setdefault(pin, []).append(label)

    leds = player.get('led_outputs')
    if not isinstance(leds, dict):
        unknown.append('LED assignments unavailable or invalid in [led_outputs]')
    else:
        for key in ('power_pin', 'connection_pin'):
            if key in leds:
                append(leds[key], f'led_outputs.{key}')
            else:
                unknown.append(f'led_outputs.{key} not specified')
    if interface is None:
        unknown.append('interface.toml unavailable: button and encoder assignments unknown')
    elif not isinstance(interface, dict):
        unknown.append('interface.toml not a TOML document')
    else:
        for section, accepted in (('buttons', None), ('encoder', {'encoder_a', 'encoder_b', 'encoder_sw'})):
            group = interface.get(section)
            if not isinstance(group, dict):
                unknown.append(f'[{section}] missing or invalid in interface.toml')
                continue
            for name, pin in group.items():
                if accepted is None or name in accepted:
                    if section == 'buttons' and pin == -1:
                        continue  # deliberately disabled button
                    append(pin, f'{section}.{name}')
            if accepted is not None:
                for name in sorted(accepted - group.keys()):
                    unknown.append(f'{section}.{name} not specified')
    return pins, unknown


def preflight_lines(player: dict, interface: dict | None, target: str,
                    *, backlight_pin: int | None = None) -> tuple[str, ...]:
    """Conservative configuration-only report; BLOCKED never means hardware tested."""
    if target not in CANDIDATES:
        raise ValueError('unknown display candidate: ' + target)
    if not isinstance(player, dict):
        raise ValueError('player configuration must be a table')
    display = player.get('display', {})
    if not isinstance(display, dict):
        raise ValueError('[display] must be a table')
    size, state, rotation = CANDIDATES[target]
    dc = display.get('dc_pin', 25)
    reset = display.get('rst_pin', 24)
    pins, unknown = _mapped_pins(player, interface)
    lines = [f'\nDisplay change preflight (READ-ONLY): {target}',
             f'  Candidate: {size}, {state}',
             f'  Current configured controller: {display.get("controller", "ssd1327")}',
             f'  Current DC/RESET: BCM{dc}/BCM{reset} (configuration, not detection)',
             '  Result: READ-ONLY - use the configuration apply path.' if target in ('ssd1327', 'ssd1351') else
             '  Result: BLOCKED - this report does not enable display switching.']
    if target in ('ssd1327', 'ssd1351'):
        lines.append('  SSD1327 and SSD1351 are supported by the live display service; apply [display] with tools/apply_display_config.py.')
    else:
        lines.append('  BLOCKER: no live player/UI, service and shutdown integration for this controller.')
        lines.append(f'  Standalone project test orientation: rotation {rotation} degrees.')
        lines.append('  Required: verify module wiring, actual orientation and rendering on hardware.')
    if target == 'st7735s':
        lines.extend(('  Documented test: RGB, panel offsets X=2/Y=1.',
                      '  Backlight: active-high, requires explicit safe GPIO assignment.'))
        if backlight_pin is None:
            unknown.append('ST7735S backlight pin not selected')
        elif type(backlight_pin) is not int or not 0 <= backlight_pin <= 27:
            lines.append(f'  CONFLICT: invalid backlight BCM pin {backlight_pin!r}.')
        else:
            if backlight_pin in SPI0 and backlight_pin != 9:
                lines.append(f'  CONFLICT: BCM{backlight_pin} reserved for SPI0; not an available BL pin.')
            elif backlight_pin == 9:
                lines.append('  UNKNOWN: BCM9 needs active boot overlay no_miso and SPI-MISO release verified separately.')
            else:
                lines.append(f'  Candidate BL: BCM{backlight_pin}; physical electrical suitability NOT verified.')
            if backlight_pin in (dc, reset):
                lines.append(f'  CONFLICT: BCM{backlight_pin} overlaps current display DC/RESET.')
            for other in pins.get(backlight_pin, []):
                lines.append(f'  CONFLICT: BCM{backlight_pin} overlaps {other}.')
    elif backlight_pin is not None:
        lines.append('  CONFLICT: --backlight-pin is only relevant to ST7735S.')
    if dc == reset:
        lines.append(f'  CONFLICT: current display DC/RESET both use BCM{dc}.')
    for name, pin in (('DC', dc), ('RESET', reset)):
        if type(pin) is not int or not 0 <= pin <= 27:
            lines.append(f'  CONFLICT: current display {name} pin is invalid.')
            continue
        if pin in SPI0:
            lines.append(f'  CONFLICT: current display {name} uses reserved SPI0 BCM{pin}.')
        for other in pins.get(pin, []):
            lines.append(f'  CONFLICT: current display {name} BCM{pin} overlaps {other}.')
    for message in unknown:
        lines.append('  UNKNOWN: ' + message)
    lines.extend(('  NOTE: config overlaps do not prove physical wiring or ownership.',
                  '  NOTE: no GPIO, SPI, boot configuration, systemd or TOML file was changed.',
                  ('  Next: apply [display] with tools/apply_display_config.py after verifying wiring.'
                   if target in ('ssd1327', 'ssd1351') else
                   '  Next: finish runtime integration and verify physical wiring before allowing selection.')))
    return tuple(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--interface-config', type=Path)
    parser.add_argument('--target', choices=tuple(CANDIDATES), required=True)
    parser.add_argument('--backlight-pin', type=int)
    args = parser.parse_args(argv)
    try:
        with args.config.open('rb') as stream:
            player = tomllib.load(stream)
        interface_path = args.interface_config or args.config.with_name('interface.toml')
        interface, problem = read_interface(interface_path)
        if problem:
            print(problem)
        for line in preflight_lines(player, interface, args.target,
                                    backlight_pin=args.backlight_pin):
            print(line)
        return 0
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
        parser.exit(1, f'Display preflight: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
