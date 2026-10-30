#!/usr/bin/env python3
"""M0.12c-r1: passive GPIO diagnostic and reproducible simulation; never controls Sonos."""
import argparse
import json
import socket
import stat
import time
from pathlib import Path

try:
    from .input_core import load_config, InputEngine
except ImportError:
    from input_core import load_config, InputEngine

try:
    from .button_commands import map_event
except ImportError:
    from button_commands import map_event

DEFAULT_CONFIG = Path(__file__).with_name('interface.toml')
DEFAULT_SOCKET = '/run/sonoscdplayer/control.sock'


def read_uid(path):
    """Read a private per-device UID file, never place the UID in argv or logs."""
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('UID file must be regular and inaccessible to group/others')
    uid = path.read_text(encoding='ascii').strip()
    import re
    if not re.fullmatch(r'[A-Za-z0-9_:-]{1,128}', uid):
        raise ValueError('invalid UID file content')
    return uid


def send_command(socket_path, command, uid=None):
    """One attempt only: never queue or replay a hardware event."""
    request = {'command': command}
    if command == 'playpause':
        if not uid:
            raise ValueError('playpause requires a private UID file')
        request['uid'] = uid
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1.0)
        client.connect(socket_path)
        client.sendall((json.dumps(request) + '\n').encode('utf-8'))
        data = bytearray()
        while b'\n' not in data and len(data) <= 16384:
            chunk = client.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
    if b'\n' not in data or len(data) > 16384:
        raise ValueError('invalid player response')
    reply = json.loads(data.split(b'\n', 1)[0])
    if not isinstance(reply, dict) or reply.get('ok') is not True:
        raise ValueError('player rejected command')
    return True


def handle_event(name, event, *, commands=False, execute=False, socket_path=DEFAULT_SOCKET, uid=None):
    command = map_event(name, event)
    print(f'{name}: {event}' + (f' -> {command}' if command else ''), flush=True)
    if not command or not commands:
        return
    if not execute:
        print(f'command preview: {command}', flush=True)
        return
    if command == 'playpause' and uid is None:
        print('command skipped: playpause UID unavailable', flush=True)
        return
    try:
        send_command(socket_path, command, uid)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        print(f'command failed ({command}): {type(exc).__name__}; not retrying', flush=True)



def pins_for(cfg):
    return {b.pin: b.pull for b in cfg.buttons.values()} | {
        cfg.encoder.a: cfg.encoder.pull, cfg.encoder.b: cfg.encoder.pull,
        cfg.encoder.switch.pin: cfg.encoder.switch.pull}


def run_sim(cfg, input_file):
    """JSON Lines: {\"ms\":0, \"levels\":{\"17\":0,...}}; first is full snapshot."""
    engine = None
    levels = {}
    with open(input_file, encoding='utf8') as f:
        for line in f:
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            step = json.loads(line)
            levels.update({int(k): int(v) for k, v in step['levels'].items()})
            if not all(pin in levels for pin in pins_for(cfg)):
                raise ValueError('First simulation sample must include all configured GPIO pins')
            if any(v not in (0, 1) for v in levels.values()):
                raise ValueError('Simulation levels must be 0 or 1')
            if engine is None:
                engine = InputEngine(cfg, levels, step['ms'])
            else:
                for name, event in engine.sample(levels, step['ms']):
                    print(f'{step["ms"]}ms {name}: {event}', flush=True)


def run_gpio(cfg, *, commands=False, execute=False, socket_path=DEFAULT_SOCKET, uid=None):
    try:
        import RPi.GPIO as GPIO
    except ImportError as exc:
        raise RuntimeError('RPi.GPIO not installed; use --simulate FILE on other systems') from exc
    pulls = {'off': GPIO.PUD_OFF, 'up': GPIO.PUD_UP, 'down': GPIO.PUD_DOWN}
    pins = pins_for(cfg)
    try:
        GPIO.setmode(GPIO.BCM)
        for pin, pull in pins.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=pulls[pull])
        sample = lambda: {pin: GPIO.input(pin) for pin in pins}
        engine = InputEngine(cfg, sample(), time.monotonic_ns() // 1_000_000)
        print('GPIO diagnostics active; Ctrl+C to exit. No Sonos commands.', flush=True)
        while True:
            for name, event in engine.sample(sample(), time.monotonic_ns() // 1_000_000):
                handle_event(name, event, commands=commands, execute=execute, socket_path=socket_path, uid=uid)
            time.sleep(cfg.poll_interval_ms / 1000)
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--gpio', action='store_true')
    group.add_argument('--simulate', type=Path, metavar='JSONL')
    p.add_argument('--commands', action='store_true', help='show allowed command mappings')
    p.add_argument('--execute', action='store_true', help='explicitly send mapped commands (requires --commands)')
    p.add_argument('--socket', default=DEFAULT_SOCKET)
    p.add_argument('--uid-file', type=Path, help='private 0600 file for playpause UID; never logged')
    args = p.parse_args()
    if args.execute and (not args.commands or not args.gpio or args.uid_file is None):
        p.error('--execute requires --commands --gpio --uid-file')
    if args.uid_file and not args.execute:
        p.error('--uid-file is only used with --execute')
    cfg = load_config(args.config)
    if args.simulate:
        run_sim(cfg, args.simulate)
    else:
        uid = read_uid(args.uid_file) if args.uid_file else None
        run_gpio(cfg, commands=args.commands, execute=args.execute, socket_path=args.socket, uid=uid)


if __name__ == '__main__':
    main()
