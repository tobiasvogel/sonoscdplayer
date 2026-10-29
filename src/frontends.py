#!/usr/bin/env python3
"""M0.14d experimental frontend supervisor: LEDs + OLED + GPIO buttons.

It never starts/stops the playback service and never accesses Sonos directly.
All children are standalone existing integrations. Hardware and button commands
require explicit opt-in. No automatic command retries or child restarts.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
MODULES = {
    'led': 'src.integrations.outputs.led_pwm',
    'oled': 'src.integrations.outputs.oled_status',
    'buttons': 'src.integrations.interfaces.interface_input',
}


def commands(args):
    """Pure argv generation; safe and testable without hardware."""
    base = [sys.executable, '-m']
    hw = ['--gpio', '--execute'] if args.hardware else []
    common = ['--socket', args.socket]
    led = base + [MODULES['led'], '--config', args.config] + common + hw + ['--seconds', '0']
    # Child processes run continuously; only the supervisor owns the optional deadline.
    oled = base + [MODULES['oled']] + common + hw + [
        '--seconds', '0', '--splash', args.splash, '--ambient-auto']
    button = base + [MODULES['buttons'], '--config', args.interface_config, '--gpio'] + common
    if args.adopt_boot_splash:
        oled.append('--adopt-boot-splash')
    if args.commands:
        button += ['--commands', '--execute', '--uid', args.uid]
    return [('led', led), ('oled', oled), ('buttons', button)]


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hardware', action='store_true', help='explicitly allow LED/OLED hardware outputs')
    p.add_argument('--buttons', action='store_true', help='enable physical input observation')
    p.add_argument('--commands', action='store_true', help='allow mapped button commands; requires --hardware --buttons --uid')
    p.add_argument('--uid', help='explicit target Sonos UID (do not put in public config)')
    p.add_argument('--config', default='config.toml')
    p.add_argument('--interface-config', default=str(ROOT / 'integrations/interfaces/interface.toml'))
    p.add_argument('--splash', default=str(ROOT / 'data/start_screen.png'))
    p.add_argument('--adopt-boot-splash', action='store_true', help='Adopt a verified firmware-initialized OLED; never use without firmware splash')
    p.add_argument('--socket', default='/tmp/sonoscdplayer-control.sock')
    p.add_argument('--seconds', type=float, default=None, help='optional positive test duration; omitted means run until SIGTERM/Ctrl+C')
    p.add_argument('--dry-run', action='store_true', help='print child commands only, never start anything')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.seconds is not None and (not __import__('math').isfinite(args.seconds) or args.seconds <= 0):
        p.error('--seconds must be finite and positive')
    if args.commands and not (args.hardware and args.buttons and args.uid):
        p.error('--commands requires --hardware --buttons and --uid')
    if args.uid and not args.commands:
        p.error('--uid is only used with --commands')
    if args.buttons and not args.hardware and not args.dry_run:
        p.error('physical buttons require --hardware; use --dry-run for preview')
    if args.hardware and not Path(args.config).is_file():
        p.error('local LED config.toml missing; specify --config')
    jobs = commands(args)
    if not args.buttons:
        jobs = [j for j in jobs if j[0] != 'buttons']
    if args.dry_run:
        for name, argv in jobs:
            print(f'{name}: {argv!r}')
        return 0
    children = []
    stop = False
    def on_signal(_signum, _frame):
        nonlocal stop
        stop = True
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        signal.signal(sig, on_signal)
    try:
        for name, argv in jobs:
            proc = subprocess.Popen(argv, cwd=ROOT, start_new_session=True)
            children.append((name, proc))
            print(f'frontend {name}: pid={proc.pid}', flush=True)
        deadline = time.monotonic() + args.seconds if args.seconds is not None else None
        while not stop and (deadline is None or time.monotonic() < deadline):
            for name, proc in children:
                code = proc.poll()
                if code is not None:
                    print(f'frontend {name}: exited ({code}); stopping other frontends', file=sys.stderr, flush=True)
                    return 1
            time.sleep(0.2)
        return 0
    finally:
        for name, proc in reversed(children):
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for name, proc in reversed(children):
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
        for sig, old in previous.items():
            signal.signal(sig, old)


if __name__ == '__main__':
    sys.exit(main())
