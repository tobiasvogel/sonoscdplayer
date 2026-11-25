#!/usr/bin/env python3
"""Apply [display]: refresh installed screen artifacts and restart the frontend.

No per-controller flags or unit edits. Preview is read-only; --apply performs
the same deployment for every registered runtime backend. Hardware must already
be connected to the configured pins. Never requests shutdown or reboot.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from sonoscdplayer.hardware.displays.pins import load_display_pins
from sonoscdplayer.system.config_paths import player_config


def apply_config(path, *, apply=False, run=subprocess.run):
    config = load_display_pins(path)
    commands = (
        ['sudo', 'systemctl', 'stop', 'sonoscdplayer-oled.service'],
        ['sh', str(ROOT / 'tools/install_screens.sh')],
        ['sudo', 'systemctl', 'restart', 'sonoscdplayer-oled.service'],
    )
    print(f'Display configuration valid: {config.controller}; ' +
          ('applying' if apply else 'preview only'))
    if apply:
        env = os.environ | {'DISPLAY_CONFIG': str(Path(path).resolve()), 'PYTHON': sys.executable}
        for command in commands:
            run(command, cwd=ROOT, env=env, check=True)
    else:
        for command in commands:
            print('Would run: ' + ' '.join(command))
    return commands


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        apply_config(args.config or player_config(), apply=args.apply)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Display configuration not applied: {exc}\n')


if __name__ == '__main__':
    main()
