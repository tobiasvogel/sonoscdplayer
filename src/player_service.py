#!/usr/bin/env python3
"""M0.12a local Unix-socket controller. No TCP control endpoint.

One command/event loop owns the controller. The socket has mode 0600. An
instance refuses to remove pre-existing socket paths (even stale ones).
No automatic PLAY or STOP on startup/exit.
"""
from __future__ import annotations

import argparse
import copy
import concurrent.futures
import threading
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import sys
import time
import tomllib

from cd_toc import read_toc
from disc_startup import inspect_initial_disc
from disc_monitor import DiscMonitor
from player_state import PlayerState, Mode
from drive_power import DrivePowerManager, load_config
from drive_identity import usb_identity, recovered_device
from player_controller import Controller, ProcessServer, routed_source_ip
from player_target_selection import SelectedTarget
from sonos_group import normalize_members
from player_play import make_url, prepare_selection
from sonos_parallel_test import detect_drive

MAX_REQUEST = 1024
COMMANDS = ('status', 'sonos-status --uid UID', 'play --uid UID', 'playpause --uid UID', 'pause', 'resume', 'next', 'previous', 'stop', 'stop --force --uid UID', 'select N', 'reset', 'help', 'set-target --uid UID', 'get-target', 'clear-target', 'play-selected', 'inspect-group-recovery', 'resolve-group-recovery')


def response(controller, *, ok, error=None, commands=None):
    result = {'ok': ok, 'status': controller.status(),
              'state': controller.status_snapshot()}
    if error is not None:
        result['error'] = error
    if commands is not None:
        result['commands'] = commands
    return result


def handle_command(controller, request, selection=None):
    """UID is command-scoped; never include it in a reply."""
    if not isinstance(request, dict) or not set(request).issubset({'command', 'uid', 'force', 'members', 'confirm'}) or 'command' not in request:
        return response(controller, ok=False, error='invalid request keys')
    raw = request['command']
    if not isinstance(raw, str) or len(raw) > 80:
        return response(controller, ok=False, error='invalid command')
    command = raw.strip().lower()
    uid = request.get('uid')
    force = request.get('force', False)
    if not isinstance(force, bool):
        return response(controller, ok=False, error='invalid force flag')
    if uid is not None and (not isinstance(uid, str) or not 1 <= len(uid) <= 128 or not re.fullmatch(r'[A-Za-z0-9_:-]+', uid)):
        return response(controller, ok=False, error='invalid UID')
    confirmed = request.get('confirm', None)
    if confirmed is not None and (command != 'resolve-group-recovery' or confirmed is not True):
        return response(controller, ok=False, error='confirmation only accepted for recovery')
    if command == 'resolve-group-recovery' and confirmed is not True:
        return response(controller, ok=False, error='explicit confirmation required')
    members = request.get('members')
    if members is not None:
        if command != 'set-target':
            return response(controller, ok=False, error='members only allowed with set-target')
        try:
            members = tuple(sorted(normalize_members(uid, members)))
        except ValueError:
            return response(controller, ok=False, error='invalid members')
    if force and (command != 'stop' or uid is None):
        return response(controller, ok=False, error='--force permitted only with stop --uid')
    if command in ('play', 'playpause', 'sonos-status', 'set-target') and uid is None:
        return response(controller, ok=False, error='PLAY requires --uid')
    if command not in ('play', 'playpause', 'sonos-status', 'set-target') and not (command == 'stop' and force) and uid is not None:
        return response(controller, ok=False, error='UID only accepted with PLAY')
    if not (command in ('status', 'sonos-status', 'play', 'playpause', 'pause', 'resume', 'next', 'previous', 'stop', 'reset', 'help', 'set-target', 'get-target', 'clear-target', 'play-selected', 'inspect-group-recovery', 'resolve-group-recovery')
            or re.fullmatch(r'select [0-9]{1,3}', command)):
        return response(controller, ok=False, error='unknown command')
    try:
        if command == 'help':
            return response(controller, ok=True, commands=COMMANDS)
        if command in ('set-target', 'get-target', 'clear-target'):
            if selection is None:
                raise RuntimeError('target selection unavailable')
            if command == 'set-target':
                selection.set(controller, uid, members)
            elif command == 'clear-target':
                selection.clear(controller)
            reply = response(controller, ok=True)
            reply['target_selection'] = selection.summary()
            return reply
        if command in ('inspect-group-recovery', 'resolve-group-recovery'):
            if command == 'inspect-group-recovery':
                inspected = controller.inspect_group_recovery()
            else:
                inspected = controller.resolve_group_recovery()
            reply = response(controller, ok=True)
            reply['group_recovery'] = inspected
            return reply
        if command == 'play-selected':
            if selection is None or selection.uid_for_explicit_action() is None:
                raise ValueError('no confirmed target')
            # This is the ONLY new route for group PLAY. It requires a separate
            # explicit command; selecting targets cannot start music.
            controller.play(selection.uid_for_explicit_action(),
                            members=selection.members_for_explicit_action())
            return response(controller, ok=True)
        if command == 'sonos-status':
            reply = response(controller, ok=True)
            reply['live_sonos'] = controller.sonos_live_status(uid)
            return reply
        if command == 'stop' and force:
            controller.force_stop(uid)
        elif command == 'reset':
            controller.reset()
        elif command == 'play':
            controller.play(uid)
        elif command == 'playpause':
            controller.playpause(uid)
        elif command != 'status':
            controller.command(command)
        return response(controller, ok=True)
    except Exception as exc:
        return response(controller, ok=False, error=f'{type(exc).__name__}: {exc}')


def disc_allows_new_play(controller, request, monitor):
    """Only explicit new PLAY needs physical TOC revalidation.

    PAUSE/RESUME and status remain side-effect free. Return False on unsafe
    or stale media; never contact Sonos on this path.
    """
    return (monitor is None or not isinstance(request, dict)
            or request.get('command') not in ('play', 'play-selected', 'playpause')
            or controller.state.mode != Mode.STOPPED
            or monitor.validate_before_play(controller))


class StatusPublisher:
    """Immutable cached read-only response; NEVER authorizes a Sonos action."""
    def __init__(self, controller, power=None):
        self._lock = threading.Lock()
        self._reply = None
        self.publish(controller, power)

    def publish(self, controller, power=None):
        # Only the serialized controller worker calls this method.
        reply = response(controller, ok=True)
        if power is not None:
            reply['state']['drive_power'] = power.snapshot(reader_active=controller.server.reader_active())
        with self._lock:
            self._reply = copy.deepcopy(reply)

    def read(self):
        with self._lock:
            return copy.deepcopy(self._reply)


def read_request(conn):
    conn.settimeout(2)
    data = bytearray()
    while len(data) <= MAX_REQUEST:
        chunk = conn.recv(min(128, MAX_REQUEST + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if b'\n' in chunk:
            break
    if len(data) > MAX_REQUEST or b'\n' not in data:
        raise ValueError('request too long or unterminated')
    return json.loads(bytes(data).split(b'\n', 1)[0].decode('utf-8'))


def send_reply(conn, reply):
    try:
        conn.sendall((json.dumps(reply, ensure_ascii=False) + '\n').encode())
    except OSError:
        pass
    finally:
        conn.close()


def restore_drive_for_explicit_action(controller, power, original_id, original_toc):
    """Rebind only the original USB drive/disc on an explicit idle action."""
    from player_state import Mode
    if controller.state.mode not in (Mode.STOPPED, Mode.ERROR):
        return False
    if (controller.owned_url or controller.server.alive() or controller._joined
            or getattr(controller, '_group_uncertain', False)):
        return False
    old = controller.server.device
    if Path(old).exists():
        if original_id is None or usb_identity(old) == original_id:
            return False
    if original_id is None:
        raise RuntimeError('original USB identity unavailable; restart required')
    new = recovered_device(original_id)
    if usb_identity(new) != original_id:
        raise RuntimeError('replacement USB drive identity changed')
    if read_toc(new) != original_toc:
        raise RuntimeError('reconnected optical drive contains different or unreadable disc')
    if power is not None:
        power.rebind_device(new, reader_active=controller.server.reader_active())
    controller.server.device = new
    print('[drive] verified USB reconnect; reader and keepalive rebound', flush=True)
    return True


def serve(controller, socket_path, *, poll_seconds=0.5, power=None, drive_id=None, drive_toc=None, disc_monitor=None):
    path = Path(socket_path).expanduser().absolute()
    if not path.parent.is_dir():
        raise ValueError('socket parent directory does not exist')
    if path.exists() or path.is_symlink():
        raise FileExistsError(f'refusing existing socket/path: {path}')
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    original_umask = os.umask(0o177)
    try:
        server.bind(str(path))
    finally:
        os.umask(original_umask)
    bound_inode = os.lstat(path).st_ino
    publisher = StatusPublisher(controller, power)
    selection = SelectedTarget()
    if hasattr(controller, 'status_hook'):
        controller.status_hook = lambda: publisher.publish(controller, power)
    pending = []
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        server.listen(16)
        server.setblocking(False)
        print(f'[M0.15a-r2] local socket ready: {path} (0600); responsive cached status', flush=True)
        # Only this one worker ever touches controller, Sonos, reader and drive power.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='player-control') as executor:
            def operate(request):
                # Check the *physical* TOC immediately before a new PLAY. The
                # 3-second cached monitor status must not authorize stale media.
                if not disc_allows_new_play(controller, request, disc_monitor):
                    reply = response(controller, ok=False,
                                     error='disc changed, unavailable or unverified; inspect status and retry PLAY')
                    publisher.publish(controller, power)
                    return reply
                if (disc_monitor is None and drive_toc is not None
                        and isinstance(request, dict)
                        and request.get('command') in ('play', 'play-selected', 'playpause', 'reset')):
                    try:
                        restore_drive_for_explicit_action(controller, power, drive_id, drive_toc)
                    except Exception as exc:
                        reply = response(controller, ok=False, error=f'drive unavailable: {type(exc).__name__}: {exc}')
                        publisher.publish(controller, power)
                        return reply
                reply = handle_command(controller, request, selection)
                publisher.publish(controller, power)
                return reply

            # Retry only when the old optical path disappears/changes. Avoid
            # probing healthy drives and preserve the normal keepalive cadence.
            next_drive_recovery = 0.0

            next_disc_check = 0.0

            def refresh():
                nonlocal next_drive_recovery, next_disc_check, drive_toc
                from player_state import Mode
                if (disc_monitor is None and drive_toc is not None and power is not None
                        and controller.state.mode in (Mode.STOPPED, Mode.ERROR)
                        and not controller.owned_url and not controller.server.alive()
                        and not controller._joined and not controller._group_uncertain
                        and not controller.server.reader_active()):
                    old = controller.server.device
                    missing = not Path(old).exists()
                    changed = (not missing and drive_id is not None
                               and usb_identity(old) != drive_id)
                    now = time.monotonic()
                    if (missing or changed) and now >= next_drive_recovery:
                        next_drive_recovery = now + max(10.0, power.config['interval'])
                        try:
                            if restore_drive_for_explicit_action(
                                    controller, power, drive_id, drive_toc) and disc_monitor is not None:
                                disc_monitor.rebind_verified(controller.server.device)
                        except (OSError, RuntimeError, ValueError) as exc:
                            print(f'[drive] idle USB recovery deferred: {type(exc).__name__}: {exc}',
                                  flush=True)
                controller.refresh()
                now = time.monotonic()
                # Safe status inspection only. NEVER touch optical media while
                # reader/stream/group ownership is attached.
                if disc_monitor is not None and now >= next_disc_check:
                    next_disc_check = now + 3.0
                    try:
                        changed = disc_monitor.poll(controller)
                        if changed:
                            drive_toc = disc_monitor.loaded_toc
                            print(f'[disc] {controller.disc_status.mode.value}', flush=True)
                    except (OSError, RuntimeError, ValueError) as exc:
                        print(f'[disc] monitor deferred: {type(exc).__name__}: {exc}', flush=True)
                if power is not None and (controller.disc_status is None or
                                          controller.disc_status.mode.value == 'READY'):
                    power.tick(reader_active=controller.server.reader_active())
                publisher.publish(controller, power)

            busy = None
            next_refresh = time.monotonic()
            while True:
                for conn, future in pending[:]:
                    if future.done():
                        pending.remove((conn, future))
                        try:
                            reply = future.result()
                        except Exception as exc:
                            reply = {'ok': False, 'error': f'worker failure: {type(exc).__name__}'}
                        send_reply(conn, reply)
                if busy is None or busy.done():
                    busy = None
                    if time.monotonic() >= next_refresh:
                        busy = executor.submit(refresh)
                        next_refresh = time.monotonic() + poll_seconds
                readable, _, _ = select.select([server], [], [], min(poll_seconds, 0.1))
                if not readable:
                    continue
                conn, _ = server.accept()
                try:
                    request = read_request(conn)
                    # Cached status is served while PLAY waits for the CD spindle.
                    # No synchronous controller read and no new Sonos checks.
                    if isinstance(request, dict) and request == {'command': 'status'}:
                        send_reply(conn, publisher.read())
                    else:
                        future = executor.submit(operate, request)
                        pending.append((conn, future))
                except (ValueError, UnicodeError, socket.timeout, OSError) as exc:
                    send_reply(conn, {'ok': False, 'error': f'bad request: {type(exc).__name__}'})
    finally:
        if hasattr(controller, 'status_hook'):
            controller.status_hook = None
        for conn, _ in pending:
            conn.close()
        server.close()
        try:
            current = os.lstat(path)
            if stat.S_ISSOCK(current.st_mode) and current.st_ino == bound_inode:
                path.unlink()
        except FileNotFoundError:
            pass


def refuse_existing_control_socket(socket_path):
    """Fail early before any drive access; serve() still checks before bind."""
    path = Path(socket_path).expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise FileExistsError(f'refusing existing socket/path: {path}')


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device', default='auto')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--config', default='config.toml')
    ap.add_argument('--socket', default='/tmp/sonoscdplayer-control.sock')
    ns = ap.parse_args(argv)
    if ns.device != 'auto' and not re.fullmatch(r'/dev/sr\d+', ns.device):
        ap.error('invalid device')
    base = Path(__file__).resolve().parent
    conf = Path(ns.config)
    if not conf.is_absolute():
        conf = base / conf
    if not conf.is_file():
        ap.error('config file not found')
    ctrl = None
    power = None
    shutdown_policy = 'stop_owned'
    try:
        refuse_existing_control_socket(ns.socket)
        disc_status, toc = inspect_initial_disc(base, requested_device=ns.device)
        device = disc_status.device or '/dev/sr0'  # placeholder, never opened without a READY TOC
        identity = usb_identity(device) if disc_status.device is not None else None
        state = prepare_selection(toc, 1) if toc is not None else PlayerState()
        with conf.open('rb') as handle:
            settings = tomllib.load(handle)
            shutdown_policy = settings.get('service', {}).get('shutdown_policy', 'stop_owned')
            startup = settings.get('audio_startup', {})
            if not isinstance(startup, dict):
                raise ValueError('audio_startup must be a TOML table')
            startup_enabled = startup.get('prebuffer_before_sonos', True)
            startup_timeout = startup.get('pcm_timeout_seconds', 45)
            if type(startup_enabled) is not bool or type(startup_timeout) not in (int, float) or not 5 <= startup_timeout <= 120:
                raise ValueError('invalid audio_startup settings')
        if shutdown_policy not in ('stop_owned', 'leave_playing'):
            raise ValueError('invalid service.shutdown_policy')
        ctrl = Controller(state, None, None, ns.port,
                          ProcessServer(base, device, conf, ns.port,
                                        startup_prebuffer=startup_enabled,
                                        startup_timeout=float(startup_timeout)), route=routed_source_ip)
        ctrl.disc_status = disc_status
        # Never issue start/keepalive motor commands when no readable disc was
        # selected. M0.19b will implement safe idle discovery and reloading.
        if toc is not None:
            power = DrivePowerManager(device, load_config(conf))
            power.start()
        print(f'[M0.19a] service ready; disc={disc_status.mode.value}; '
              'no Sonos action until explicit PLAY', flush=True)
        def on_verified_rebind(new_device):
            # Same original USB serial, validated by DiscMonitor; only idle.
            if power is not None:
                power.rebind_device(new_device,
                                    reader_active=ctrl.server.reader_active())
            ctrl.server.device = new_device
            print(f'[drive] verified USB identity rebind: {new_device}', flush=True)

        monitor = (DiscMonitor(disc_status.device, identity,
                               on_rebind=on_verified_rebind)
                   if disc_status.device is not None else None)
        if monitor is not None:
            monitor.loaded_toc = toc  # verified startup TOC, never stale
        serve(ctrl, ns.socket, power=power, drive_id=identity,
              drive_toc=toc, disc_monitor=monitor)
    except KeyboardInterrupt:
        print('\n[M0.12b-r7] shutdown requested', flush=True)
    except Exception as exc:
        print(f'[M0.12a] service failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    finally:
        if ctrl:
            result = ctrl.shutdown_owned(shutdown_policy)
            print(f'[M0.12b-r7] shutdown: {result}', flush=True)
            if result['action'] == 'not_stopped':
                print('[M0.12b-r7] WARNING: Sonos may retain a stream URI; no unverified STOP sent', flush=True)
            ctrl.close()
        if power is not None:
            power.close()
    return 0


if __name__ == '__main__':
    sys.exit(run())
