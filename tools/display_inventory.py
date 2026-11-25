#!/usr/bin/env python3
"""Read-only display configuration inventory. No GPIO, SPI or systemd operations.

This report describes *configuration*, not electrically detected hardware.
Profiles which are not connected to the player must not be offered as a
working replacement for the configured display service.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from sonoscdplayer.hardware.displays.pins import DisplayPins  # noqa: E402
from sonoscdplayer.ui.displays.profiles import PROFILES  # noqa: E402


def pin_overlap_lines(config: dict, interface: dict | None = None) -> tuple[str, ...]:
    """Report configured BCM collisions; never imply an electrical probe.

    None means interface.toml was unavailable, NOT that its pins are free.
    Only explicitly mapped input pins are examined (no example defaults).
    """
    display = config.get('display', {})
    led = config.get('led_outputs', {})
    if not isinstance(led, dict):
        return ('WARNING: [led_outputs] is not a table; pin audit incomplete.',)
    if interface is not None and not isinstance(interface, dict):
        return ('WARNING: interface.toml is not a TOML table; pin audit incomplete.',)
    pins: dict[int, list[str]] = {}

    def add(value, label):
        if type(value) is int and 0 <= value <= 27:
            pins.setdefault(value, []).append(label)

    defaults = DisplayPins()
    for key in ('dc_pin', 'rst_pin'):
        add(display.get(key, getattr(defaults, key)), 'display.' + key)
    for key in ('power_pin', 'connection_pin'):
        if key in led:
            add(led[key], 'led_outputs.' + key)
    if interface is not None:
        for section in ('buttons', 'encoder'):
            group = interface.get(section, {})
            if not isinstance(group, dict):
                return (f'WARNING: interface [{section}] invalid; pin audit incomplete.',)
            for name, value in group.items():
                if section == 'buttons' or name in ('encoder_a', 'encoder_b', 'encoder_sw'):
                    add(value, section + '.' + name)
    results = ['BCM pin overlap audit (configuration only):']
    overlaps = [(pin, names) for pin, names in sorted(pins.items()) if len(names) > 1]
    if overlaps:
        for pin, names in overlaps:
            results.append(f'  WARNING: BCM{pin}: ' + ', '.join(names))
    else:
        results.append('  No overlaps among the inspected configured pins.')
    if interface is None:
        results.append('  NOTE: interface.toml not inspected; button/encoder collisions UNKNOWN.')
    results.append('  NOTE: SPI0 BCM7/8/9/10/11 are reserved or multifunction pins; '
                   'GPIO9 backlight needs explicit no_miso verification.')
    return tuple(results)


def inventory_lines(config: dict, interface: dict | None = None) -> tuple[str, ...]:
    """Describe the installed settings without probing or changing hardware."""
    if not isinstance(config, dict):
        raise ValueError('configuration must be a TOML table')
    configured = config.get('display', {})
    if not isinstance(configured, dict):
        raise ValueError('[display] must be a TOML table')
    defaults = DisplayPins()
    allowed = DisplayPins.__dataclass_fields__
    extras = sorted(set(configured) - set(allowed))
    lines = [
        '\nDisplay hardware inventory (read-only)',
        'Source: [display] in player.toml; omitted keys use SSD1327 runtime defaults.',
    ]
    for name in allowed:
        value = configured.get(name, getattr(defaults, name))
        label = 'configured' if name in configured else 'runtime default'
        lines.append(f'  {name}: {value} ({label})')
    if extras:
        lines.append('WARNING: unknown display keys: ' + ', '.join(extras))
    try:
        if extras:
            raise ValueError('unknown display keys')
        DisplayPins(**configured)
    except (ValueError, TypeError) as exc:
        lines.append(f'WARNING: configuration is NOT accepted by the current display runtime: {exc}')
    else:
        lines.append('Display configuration: accepted by current runtime validation.')
    lines.append('Known display profiles (catalogue only, not automatic hardware detection):')
    for key, profile in PROFILES.items():
        state = ('player-integrated' if profile.driver_ready else
                 'diagnostic/driver only; not connected to player')
        lines.append(f'  {key}: {profile.controller}, '
                     f'{profile.width}x{profile.height}, {profile.color_mode}; {state}')
    lines.extend(pin_overlap_lines(config, interface))
    lines.extend((
        'Note: catalogue dimensions show tested landscape canvases (rotation 270)',
        '      for ST7735S and SH1107; profile keys retain native identifiers.',
        'No display, pins, rotation, systemd unit or configuration was changed.',
    ))
    return tuple(lines)



def read_interface_config(path: Path) -> tuple[dict | None, str]:
    """Read the actual input mapping without guessing missing hardware pins.

    The report remains usable when the interface mapping is missing or invalid.
    None is explicitly unknown, never proof of a conflict-free installation.
    """
    try:
        with Path(path).open('rb') as stream:
            value = tomllib.load(stream)
    except FileNotFoundError:
        return None, f'WARNING: {path} missing; input pin audit INCOMPLETE.'
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, f'WARNING: cannot read {path}: {exc}; input pin audit INCOMPLETE.'
    if not isinstance(value, dict):
        return None, f'WARNING: {path} is not a TOML table; input pin audit INCOMPLETE.'
    for table in ('buttons', 'encoder'):
        if table in value and not isinstance(value[table], dict):
            return None, f'WARNING: invalid [{table}] in {path}; input pin audit INCOMPLETE.'
    return value, f'Input pin mapping: {path} (read-only).'


def full_inventory_lines(config: dict, interface_path: Path) -> tuple[str, ...]:
    """Create a complete report using the same path as the live button service."""
    interface, source_line = read_interface_config(interface_path)
    report = list(inventory_lines(config, interface))
    report.insert(2, source_line)
    report.extend((
        'Runtime selection: SSD1327 and SSD1351; apply [display] with tools/apply_display_config.py.',
        'This read-only report does not apply configuration or detect wiring.',
    ))
    return tuple(report)

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=None,
                        help='player.toml (default: installed configuration path)')
    parser.add_argument('--interface-config', type=Path, default=None,
                        help='interface.toml for read-only button/encoder pin audit (default: same directory)')
    args = parser.parse_args(argv)
    if args.config is None:
        from sonoscdplayer.system.config_paths import player_config
        args.config = player_config()
    try:
        with args.config.open('rb') as stream:
            config = tomllib.load(stream)
        interface_path = args.interface_config or args.config.with_name('interface.toml')
        for line in full_inventory_lines(config, interface_path):
            print(line)
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
        print(f'Display inventory: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
