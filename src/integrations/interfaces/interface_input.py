#!/usr/bin/env python3
"""GPIO diagnostic with explicit opt-in, bounded one-shot player commands."""
import argparse
import queue
import json
import socket
import stat
import threading
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

try:
    from .safe_button_preview import SelectedTargetPreview
except ImportError:
    from safe_button_preview import SelectedTargetPreview

try:
    from .safe_button_execute import SelectedTargetExecutor
except ImportError:
    from safe_button_execute import SelectedTargetExecutor

try:
    from .power_gesture import PowerCoordinator
except ImportError:
    from power_gesture import PowerCoordinator

try:
    from .connect_gesture import ConnectGesture
except ImportError:
    from connect_gesture import ConnectGesture

try:
    import config_paths
except ImportError:  # executed as a plain script from its own directory
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config_paths

DEFAULT_SOCKET = '/run/sonoscdplayer/control.sock'

# PLAY can spend up to 120 seconds prebuffering before acknowledging.
# Response deadlines are total wall-clock bounds; no request is retried.
TRANSPORT_RESPONSE_TIMEOUTS = {
    'play-selected': 130.0,
    'pause': 10.0,
    'resume': 10.0,
    'stop': 10.0,
    'next': 130.0,
    'previous': 130.0,
    'eject': 10.0,
    'drive-activity': 30.0,
}
DEFAULT_COMMAND_RESPONSE_TIMEOUT = 1.0


def read_uid(path):
    """Read a private per-device UID file, never place the UID in argv or logs."""
    if path.is_symlink():
        raise ValueError('UID file must not be a symlink')
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
    response_timeout = TRANSPORT_RESPONSE_TIMEOUTS.get(
        command, DEFAULT_COMMAND_RESPONSE_TIMEOUT)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        # Fail quickly on connect/send; allow bounded time for player work.
        client.settimeout(1.0)
        client.connect(socket_path)
        client.sendall((json.dumps(request) + '\n').encode('utf-8'))
        deadline = time.monotonic() + response_timeout
        data = bytearray()
        while b'\n' not in data and len(data) <= 16384:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('player response deadline exceeded')
            client.settimeout(remaining)
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


class CommandDispatcher:
    """At most one in-flight command; never queue, block GPIO, or retry.

    A daemon worker is intentionally abandoned at shutdown. The player may already
    have accepted a command when the local socket times out: never resend it.
    """
    def __init__(self, socket_path=DEFAULT_SOCKET, uid=None, sender=send_command):
        self.socket_path = socket_path
        self.uid = uid
        self.sender = sender
        self._lock = threading.Lock()
        self._busy = False

    def submit(self, command):
        if command == 'playpause' and self.uid is None:
            print('command skipped: playpause UID unavailable', flush=True)
            return False
        with self._lock:
            if self._busy:
                print(f'command dropped ({command}): dispatcher busy', flush=True)
                return False
            self._busy = True

        def perform():
            try:
                self.sender(self.socket_path, command, self.uid)
            except Exception as exc:
                print(f'command failed ({command}): {type(exc).__name__}; not retrying', flush=True)
            finally:
                with self._lock:
                    self._busy = False

        try:
            threading.Thread(target=perform, name='button-command', daemon=True).start()
        except Exception:
            with self._lock:
                self._busy = False
            raise
        return True



class DriveActivityPublisher:
    """Bounded, best-effort activity RPC; no GPIO-thread socket I/O."""

    def __init__(self, socket_path, *, sender=send_command,
                 max_pending=32):
        self.socket_path = socket_path
        self.sender = sender
        self._queue = queue.Queue(maxsize=max_pending)
        self._closed = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError('drive activity publisher already started')
        self._thread = threading.Thread(
            target=self._run,
            name='drive-activity',
            daemon=True,
        )
        self._thread.start()

    def enqueue(self):
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait(None)
            return True
        except queue.Full:
            return False

    def _run(self):
        while not self._closed.is_set():
            try:
                self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if self._closed.is_set():
                break
            try:
                self.sender(self.socket_path, 'drive-activity', None)
            except Exception as exc:
                print(
                    'drive activity: unavailable ('
                    + type(exc).__name__ + '); not retrying',
                    flush=True,
                )

    def close(self):
        self._closed.set()


def should_publish_drive_activity(name, event, *, safe_executor):
    """Avoid duplicate activity for PLAY/PAUSE with synchronous preflight."""
    physical = (
        event == 'pressed'
        or (name == 'encoder' and event in ('left', 'right'))
    )
    if not physical:
        return False
    command = map_event(name, event)

    # Every physical control counts as activity, including EJECT.
    # EJECT authorization remains guarded separately by INP-03.
    # Safe executor performs synchronous wake before every mapped
    # transport action. Other physical inputs use the async publisher.
    return not (
        safe_executor
        and command in (
            'playpause', 'stop', 'next', 'previous', 'eject'
        )
    )


def handle_event(name, event, *, commands=False, execute=False, socket_path=DEFAULT_SOCKET, uid=None, dispatcher=None, target_preview=None, target_executor=None):
    command = map_event(name, event)
    print(f'{name}: {event}' + (f' -> {command}' if command else ''), flush=True)
    if not command or not commands:
        return
    if not execute:
        if command == 'playpause' and target_preview is not None:
            target_preview.submit()
        else:
            print(f'command preview: {command}', flush=True)
        return
    if target_executor is not None:
        # Explicitly allow only policy-checked transport actions. Other hardware
        # buttons remain blocked in safe execute mode.
        if command == 'playpause':
            target_executor.submit()
        elif command in ('stop', 'next', 'previous', 'eject'):
            target_executor.submit(command)
        else:
            print(f'command skipped in safe execute mode: {command}', flush=True)
        return
    if command == 'playpause' and uid is None:
        print('command skipped: playpause UID unavailable', flush=True)
        return
    if dispatcher is not None:
        dispatcher.submit(command)
        return
    raise RuntimeError('executing commands requires a nonblocking dispatcher')



def make_power_shutdown(enabled, requester=None, start=None):
    """Return the long-press callback; it never blocks the GPIO sampling loop."""
    if enabled and requester is None:
        from power_action import PowerRequester
        requester = PowerRequester()
    start = start or (lambda target: threading.Thread(
        target=target, name='power-shutdown', daemon=True).start())

    def shutdown():
        if not enabled:
            print('power button held: shutdown preview only (--power-actions not set)', flush=True)
            return
        print('power button held: requesting poweroff', flush=True)
        start(lambda: requester.request('poweroff'))
    return shutdown


def publish_connect_event(gesture, name, event, now_ms, publish, *,
                          separate_speaker_button=False):
    """Publish UI-only semantic gestures, never a network or Sonos action."""
    if name == 'speaker_select':
        if event == 'pressed' and separate_speaker_button:
            publish('speaker_select', 'pressed')
        return name == 'speaker_select'
    if name != 'connect':
        return False
    if event == 'pressed':
        gesture.press(now_ms)
        return True
    if event == 'released':
        action = gesture.release(now_ms)
        if action is not None:
            if action == 'status' and not separate_speaker_button:
                publish('speaker_select', 'pressed')
            else:
                publish('connect', action)
        return True
    return False


def tick_connect_event(gesture, now_ms, publish):
    """Emit bounded countdown updates while the CONNECT button is held."""
    action = gesture.tick(now_ms)
    if action is not None:
        publish('connect', action)
    return action


def pins_for(cfg):
    return {b.pin: b.pull for b in cfg.buttons.values()
            if isinstance(b.pin, int)} | {
        cfg.encoder.a: cfg.encoder.pull, cfg.encoder.b: cfg.encoder.pull,
        cfg.encoder.switch.pin: cfg.encoder.switch.pull}


def input_sources(cfg):
    return set(pins_for(cfg)) | {
        button.pin for button in cfg.buttons.values()
        if isinstance(button.pin, str)
    }


def run_sim(cfg, input_file):
    """JSON Lines input levels keyed by BCM pin or 'sr0:0'; first snapshot full."""
    engine = None
    levels = {}
    with open(input_file, encoding='utf8') as f:
        for line in f:
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            step = json.loads(line)
            levels.update({int(k) if k.isdecimal() else k: int(v)
                           for k, v in step['levels'].items()})
            if not input_sources(cfg).issubset(levels):
                raise ValueError('First simulation sample must include all configured input sources')
            if any(v not in (0, 1) for v in levels.values()):
                raise ValueError('Simulation levels must be 0 or 1')
            if engine is None:
                engine = InputEngine(cfg, levels, step['ms'])
            else:
                for name, event in engine.sample(levels, step['ms']):
                    print(f'{step["ms"]}ms {name}: {event}', flush=True)


def run_gpio(cfg, *, commands=False, execute=False, socket_path=DEFAULT_SOCKET, uid=None, ui_events_socket=None, target_preview_enabled=False, target_execute_enabled=False,
             drive_activity_enabled=False, power_actions=False,
             connect_gestures=False):
    try:
        import RPi.GPIO as GPIO
    except ImportError as exc:
        raise RuntimeError('RPi.GPIO not installed; use --simulate FILE on other systems') from exc
    pulls = {'off': GPIO.PUD_OFF, 'up': GPIO.PUD_UP, 'down': GPIO.PUD_DOWN}
    pins = pins_for(cfg)
    try:
        from .shift_register_74hc165 import ShiftRegisterReader
    except ImportError:
        from shift_register_74hc165 import ShiftRegisterReader
    shift_reader = ShiftRegisterReader(GPIO, cfg.shift_registers)
    # Optional UI transport is started once, outside the GPIO sampling loop.
    # The producer uses put_nowait only; the worker owns all socket I/O.
    publisher = None
    activity_publisher = (
        DriveActivityPublisher(socket_path)
        if drive_activity_enabled else None
    )
    if ui_events_socket is not None:
        from sonos_ui.ui_event_bridge import AsyncEventPublisher
        publisher = AsyncEventPublisher(ui_events_socket)
    try:
        GPIO.setmode(GPIO.BCM)
        for pin, pull in pins.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=pulls[pull])
        shift_reader.setup()
        def sample():
            return {pin: GPIO.input(pin) for pin in pins} | shift_reader.sample()
        engine = InputEngine(cfg, sample(), time.monotonic_ns() // 1_000_000)
        dispatcher = None  # CLI execution only uses the safe selected-target path.
        target_preview = SelectedTargetPreview(socket_path) if target_preview_enabled else None
        target_executor = (
            SelectedTargetExecutor(
                socket_path,
                sender=send_command,
                activity_before_read=drive_activity_enabled,
            )
            if target_execute_enabled else None
        )
        if publisher is not None:
            publisher.start()
        if activity_publisher is not None:
            activity_publisher.start()
        power = PowerCoordinator(
            cfg.power_hold_ms,
            publish=(publisher.enqueue if publisher is not None else (lambda *_: False)),
            shutdown=make_power_shutdown(power_actions))
        connect = ConnectGesture() if connect_gestures else None
        separate_speaker_button = 'speaker_select' in cfg.buttons
        print('GPIO command mode active.' if execute else 'GPIO diagnostics active; Ctrl+C to exit. No Sonos commands.', flush=True)
        while True:
            now_ms = time.monotonic_ns() // 1_000_000
            for name, event in engine.sample(sample(), now_ms):
                power.event(name, event, now_ms)
                if connect is not None and name in ('connect', 'speaker_select'):
                    publish_connect_event(
                        connect, name, event, now_ms,
                        publisher.enqueue if publisher is not None
                        else (lambda *_: False),
                        separate_speaker_button=separate_speaker_button)
                elif publisher is not None:
                    publisher.enqueue(name, event)
                if (
                    activity_publisher is not None
                    and should_publish_drive_activity(
                        name, event,
                        safe_executor=target_executor is not None,
                    )
                ):
                    activity_publisher.enqueue()
                handle_event(name, event, commands=commands, execute=execute, socket_path=socket_path, uid=uid, dispatcher=dispatcher, target_preview=target_preview, target_executor=target_executor)
            tick_ms = time.monotonic_ns() // 1_000_000
            power.tick(tick_ms)
            if connect is not None:
                tick_connect_event(
                    connect, tick_ms,
                    publisher.enqueue if publisher is not None
                    else (lambda *_: False))
            time.sleep(cfg.poll_interval_ms / 1000)
    except KeyboardInterrupt:
        pass
    finally:
        if publisher is not None:
            publisher.close()
        if activity_publisher is not None:
            activity_publisher.close()
        GPIO.cleanup()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=None,
                   help='interface TOML (default: interface.toml in the standard config directory)')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--gpio', action='store_true')
    group.add_argument('--simulate', type=Path, metavar='JSONL')
    p.add_argument('--commands', action='store_true', help='show allowed command mappings')
    p.add_argument('--execute', action='store_true', help='execute safe PLAY/PAUSE, STOP, track changes and idle-only EJECT (requires --selected-target-execute)')
    p.add_argument('--selected-target-preview', action='store_true',
                   help='read-only status/get-target planning for PLAY/PAUSE; never sends transport')
    p.add_argument('--selected-target-execute', action='store_true',
                   help='with --execute: safe state-checked one-shot transport actions')
    p.add_argument('--socket', default=DEFAULT_SOCKET)
    p.add_argument(
        '--drive-activity-events',
        action='store_true',
        help='opt-in asynchronous physical activity RPC for idle drive wake',
    )
    p.add_argument('--connect-gestures', action='store_true',
                   help='opt-in semantic CONNECT/network UI gestures; no network writes')
    p.add_argument('--power-actions', action='store_true',
                   help='with --gpio: holding the power button really powers the system off '
                        '(needs the polkit rule from docs/POWER_BUTTON.md); otherwise preview only')
    p.add_argument('--ui-events-socket', default=None,
                   help='optional nonblocking read-only UI event observer')
    p.add_argument('--uid-file', type=Path, help='private 0600 file for playpause UID; never logged')
    args = p.parse_args()
    if args.config is None:
        args.config = config_paths.interface_config()
    if args.execute and (not args.commands or not args.gpio):
        p.error('--execute requires --commands --gpio')
    if args.selected_target_preview and (not args.gpio or not args.commands or args.execute):
        p.error('--selected-target-preview requires --gpio --commands without --execute')
    if args.execute and not args.selected_target_execute:
        p.error('--execute requires --selected-target-execute (legacy dispatch disabled)')
    if args.selected_target_execute and (not args.gpio or not args.commands or
                                         not args.execute or args.selected_target_preview):
        p.error('--selected-target-execute requires --gpio --commands --execute without --selected-target-preview')
    if args.uid_file:
        p.error('--uid-file is not used by safe selected-target execution')
    if args.drive_activity_events and not args.gpio:
        p.error('--drive-activity-events requires --gpio')
    if args.connect_gestures and (not args.gpio or args.ui_events_socket is None):
        p.error('--connect-gestures requires --gpio and --ui-events-socket')
    if args.power_actions and not args.gpio:
        p.error('--power-actions requires --gpio')
    if args.ui_events_socket is not None and not args.gpio:
        p.error('--ui-events-socket requires --gpio')
    cfg = load_config(args.config)
    if args.simulate:
        run_sim(cfg, args.simulate)
    else:
        uid = read_uid(args.uid_file) if args.uid_file else None
        run_gpio(cfg, commands=args.commands, execute=args.execute, socket_path=args.socket,
                 uid=uid, ui_events_socket=args.ui_events_socket,
                 target_preview_enabled=args.selected_target_preview,
                 target_execute_enabled=args.selected_target_execute,
                 drive_activity_enabled=args.drive_activity_events,
                 power_actions=args.power_actions,
                 connect_gestures=args.connect_gestures)


if __name__ == '__main__':
    main()
