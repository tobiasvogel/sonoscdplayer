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
from drive_power import DrivePowerManager, load_config
from player_controller import Controller, ProcessServer, routed_source_ip
from player_play import make_url, prepare_selection
from sonos_parallel_test import detect_drive

MAX_REQUEST = 256
COMMANDS = ('status', 'sonos-status --uid UID', 'play --uid UID', 'playpause --uid UID', 'pause', 'resume', 'next', 'previous', 'stop', 'stop --force --uid UID', 'select N', 'reset', 'help')


def response(controller, *, ok, error=None, commands=None):
    result = {'ok': ok, 'status': controller.status(),
              'state': controller.status_snapshot()}
    if error is not None:
        result['error'] = error
    if commands is not None:
        result['commands'] = commands
    return result


def handle_command(controller, request):
    """UID is command-scoped; never include it in a reply."""
    if not isinstance(request, dict) or not set(request).issubset({'command', 'uid', 'force'}) or 'command' not in request:
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
    if force and (command != 'stop' or uid is None):
        return response(controller, ok=False, error='--force permitted only with stop --uid')
    if command in ('play', 'playpause', 'sonos-status') and uid is None:
        return response(controller, ok=False, error='PLAY requires --uid')
    if command not in ('play', 'playpause', 'sonos-status') and not (command == 'stop' and force) and uid is not None:
        return response(controller, ok=False, error='UID only accepted with PLAY')
    if not (command in ('status', 'sonos-status', 'play', 'playpause', 'pause', 'resume', 'next', 'previous', 'stop', 'reset', 'help')
            or re.fullmatch(r'select [0-9]{1,3}', command)):
        return response(controller, ok=False, error='unknown command')
    try:
        if command == 'help':
            return response(controller, ok=True, commands=COMMANDS)
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


def serve(controller, socket_path, *, poll_seconds=0.5, power=None):
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
                reply = handle_command(controller, request)
                publisher.publish(controller, power)
                return reply

            def refresh():
                controller.refresh()
                if power is not None:
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
        device = detect_drive(base) if ns.device == 'auto' else ns.device
        state = prepare_selection(read_toc(device), 1)
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
        power = DrivePowerManager(device, load_config(conf))
        power.start()
        print('[M0.12b-r7] ready; no Sonos action until explicit PLAY', flush=True)
        serve(ctrl, ns.socket, power=power)
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
