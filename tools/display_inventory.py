#!/usr/bin/env python3
"""Read-only display configuration inventory. No GPIO, SPI or systemd operations.

This report describes *configuration*, not electrically detected hardware.
Profiles which are not connected to the player must not be offered as a
working replacement for the installed SSD1327 display service.
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


def inventory_lines(config: dict) -> tuple[str, ...]:
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
        lines.append(f'WARNING: configuration is NOT accepted by the current SSD1327 runtime: {exc}')
    else:
        lines.append('SSD1327 configuration: accepted by current runtime validation.')
    lines.append('Known display profiles (catalogue only, not automatic hardware detection):')
    for key, profile in PROFILES.items():
        state = ('player-integrated' if profile.driver_ready else
                 'diagnostic/driver only; not connected to player')
        lines.append(f'  {key}: {profile.controller}, '
                     f'{profile.width}x{profile.height}, {profile.color_mode}; {state}')
    lines.extend((
        'Note: catalogue dimensions are profile orientation, not necessarily the tested',
        '      landscape orientation; consult docs/DISPLAYS.md for tested modules.',
        'No display, pins, rotation, systemd unit or configuration was changed.',
    ))
    return tuple(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=None,
                        help='player.toml (default: installed configuration path)')
    args = parser.parse_args(argv)
    if args.config is None:
        from sonoscdplayer.system.config_paths import player_config
        args.config = player_config()
    try:
        with args.config.open('rb') as stream:
            config = tomllib.load(stream)
        for line in inventory_lines(config):
            print(line)
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
        print(f'Display inventory: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
