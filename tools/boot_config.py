#!/usr/bin/env python3
"""Keep the SonosCDPlayer lines in the Raspberry Pi config.txt up to date.

The firmware sets these pins before Linux starts, from player.toml:
  power LED on (``op,dh``), connection LED off (``op,dl``), and the OLED
  RESET line high (``op,dh``) so that the boot splash can talk to the
  controller; plus ``splash_screen=`` for the firmware boot splash.

The lines live in one marked block at the end of config.txt (under ``[all]``).
Identical lines found elsewhere are moved into the block; a different setting
for one of these pins (or another splash_screen) is reported and nothing is
changed, so a manual setup is never silently overridden.

Usage:
  boot_config.py --player-config P --config-txt C [--splash NAME] --output NEW
Exit codes: 0 written to --output (also when unchanged, see stdout), 3 conflict.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

BEGIN = '# BEGIN sonoscdplayer: managed by tools/install_screens.sh, do not edit'
END = '# END sonoscdplayer'
GPIO_LINE = re.compile(r'^\s*gpio\s*=\s*([0-9,\-\s]+?)\s*=')
SPLASH_LINE = re.compile(r'^\s*splash_screen\s*=')
SPLASH_NAME = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


class ConflictError(ValueError):
    pass


def desired_lines(player_config, splash=None):
    from sonoscdplayer.hardware.displays.pins import load_display_pins
    from sonoscdplayer.hardware.leds.led_service import load_config
    leds = load_config(str(player_config))
    display = load_display_pins(player_config)
    lines = [f'gpio={leds.power_pin}=op,dh',        # power LED on from the start
             f'gpio={leds.connection_pin}=op,dl',   # connection LED off
             f'gpio={display.rst_pin}=op,dh']       # OLED RESET released for the splash
    if splash is not None:
        if not SPLASH_NAME.fullmatch(splash):
            raise ValueError('invalid splash file name: ' + splash)
        lines.append(f'splash_screen={splash}')
    return lines


def _pins(spec):
    pins = set()
    for part in spec.replace(' ', '').split(','):
        if '-' in part:
            first, last = part.split('-', 1)
            pins.update(range(int(first), int(last) + 1))
        elif part:
            pins.add(int(part))
    return pins


def update(text, lines):
    """Return config.txt text with exactly one managed block holding lines."""
    ours = set()
    for line in lines:
        match = GPIO_LINE.match(line)
        if match:
            ours |= _pins(match.group(1))
    kept, conflicts, inside = [], [], False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped == BEGIN:
            inside = True
            continue
        if stripped == END:
            inside = False
            continue
        if inside:
            continue
        match = GPIO_LINE.match(raw)
        touches = ((match and _pins(match.group(1)) & ours)
                   or (SPLASH_LINE.match(raw) and any(SPLASH_LINE.match(l) for l in lines)))
        if touches and not stripped.startswith('#'):
            if stripped not in lines and not (stripped == 'splash_screen=ssd1327.bin' and 'splash_screen=display.bin' in lines):
                conflicts.append(stripped)
            continue  # identical: moved into the managed block
        kept.append(raw)
    if inside:
        raise ConflictError('managed block in config.txt is not closed (missing END line)')
    if conflicts:
        raise ConflictError('config.txt sets these differently: ' + '; '.join(conflicts))
    while kept and not kept[-1].strip():
        kept.pop()
    block = ['', BEGIN, '[all]', *lines, END]
    return '\n'.join(kept + block) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--player-config', type=Path, required=True)
    parser.add_argument('--config-txt', type=Path, required=True)
    parser.add_argument('--splash', default=None, help='firmware splash file name in the boot folder')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    current = args.config_txt.read_text(encoding='utf-8')
    try:
        new = update(current, desired_lines(args.player_config, args.splash))
    except ConflictError as exc:
        print('config.txt NOT changed: ' + str(exc), file=sys.stderr)
        print('Remove or correct those lines by hand, then run the script again.', file=sys.stderr)
        return 3
    args.output.write_text(new, encoding='utf-8')
    print('unchanged' if new == current else 'changed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
