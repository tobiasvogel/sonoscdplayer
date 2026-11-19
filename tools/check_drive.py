#!/usr/bin/env python3
"""Check whether a CD drive works with SonosCDPlayer.

Insert an audio CD, then run from the repository root:

    python3 tools/check_drive.py

The check reads audio, spins the drive down and up again and finally ejects
the CD (skip that with --no-eject). It takes about two minutes. The report at
the end can be pasted into a GitHub issue; it names the drive model but no
serial number, CD title or other personal data. See docs/DRIVE_CHECK.md.

Exit code: 0 suitable, 1 suitable with limitations, 2 not suitable,
3 the check could not run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from disc_startup import optical_devices  # noqa: E402
from drive_access import drive_lock  # noqa: E402
from drive_check import DriveChecker, format_report  # noqa: E402
import version  # noqa: E402

COULD_NOT_RUN = 3
PLAYER_SERVICE = 'sonoscdplayer-player.service'


def player_service_running(run_command=subprocess.run) -> bool:
    """True if the player service is active and would use the drive too."""
    try:
        result = run_command(['systemctl', 'is-active', '--quiet', PLAYER_SERVICE],
                             capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description='Check whether a CD drive works with SonosCDPlayer. '
                    'Insert an audio CD first.')
    parser.add_argument('--device', default='auto',
                        help='drive device, e.g. /dev/sr0 (default: the first CD drive found)')
    parser.add_argument('--no-eject', action='store_true',
                        help='do not eject the CD at the end (the eject check is skipped)')
    parser.add_argument('--json', action='store_true',
                        help='print the report as JSON instead of text')
    return parser.parse_args(argv)


def choose_device(requested: str, *, find_devices=optical_devices) -> str | None:
    if requested != 'auto':
        return requested
    devices = find_devices()
    return devices[0] if devices else None


def main(argv=None, *, run_command=subprocess.run, find_devices=optical_devices,
         checker_factory=DriveChecker, lock=drive_lock) -> int:
    arguments = parse_arguments(argv)
    device = choose_device(arguments.device, find_devices=find_devices)
    if device is None:
        print('No CD drive found. Is it connected and shown as /dev/sr0?', file=sys.stderr)
        return COULD_NOT_RUN
    if player_service_running(run_command):
        print(f'The player is running and uses the drive. Stop it first:\n'
              f'  sudo systemctl stop {PLAYER_SERVICE}\n'
              f'and start it again afterwards:\n'
              f'  sudo systemctl start {PLAYER_SERVICE}', file=sys.stderr)
        return COULD_NOT_RUN
    try:
        with lock(device, blocking=False) as acquired:
            if not acquired:
                print(f'{device} is in use by another SonosCDPlayer process.', file=sys.stderr)
                return COULD_NOT_RUN
            checker = checker_factory(device, run_command=run_command)
            progress = (lambda title: None) if arguments.json else (
                lambda title: print(f'... {title}', file=sys.stderr, flush=True))
            report = checker.run(software_version=version.release(),
                                 include_eject=not arguments.no_eject,
                                 progress=progress)
    except (ValueError, PermissionError) as exc:
        print(f'Cannot use {device}: {exc}', file=sys.stderr)
        return COULD_NOT_RUN
    if arguments.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print()
        print(format_report(report))
        print('\nPlease share this report (it contains no personal data) at\n'
              'https://github.com/tobiasvogel/sonoscdplayer/issues')
    return report.exit_code


if __name__ == '__main__':
    raise SystemExit(main())
