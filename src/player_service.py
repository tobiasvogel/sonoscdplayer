#!/usr/bin/env python3
"""M0.12a local Unix-socket controller. No TCP control endpoint.

One command/event loop owns the controller. The socket has mode 0600. An
instance refuses to remove pre-existing socket paths (even stale ones).
No automatic PLAY or STOP on startup/exit.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import sys
import time

from cd_toc import read_toc
from player_controller import Controller, ProcessServer, routed_source_ip
from player_play import make_url, prepare_selection
from sonos_parallel_test import detect_drive

MAX_REQUEST = 256
COMMANDS = ('status', 'play --uid UID', 'next', 'previous', 'stop', 'select N', 'help')


def handle_command(controller, request):
    """UID is command-scoped; never include it in a reply."""
    if not isinstance(request, dict) or not set(request).issubset({'command', 'uid'}) or 'command' not in request:
        return {'ok': False, 'error': 'invalid request keys', 'status': controller.status()}
    raw = request['command']
    if not isinstance(raw, str) or len(raw) > 80:
        return {'ok': False, 'error': 'invalid command', 'status': controller.status()}
    command = raw.strip().lower()
    uid = request.get('uid')
    if uid is not None and (not isinstance(uid, str) or not 1 <= len(uid) <= 128 or not re.fullmatch(r'[A-Za-z0-9_:-]+', uid)):
        return {'ok': False, 'error': 'invalid UID', 'status': controller.status()}
    if command == 'play' and uid is None:
        return {'ok': False, 'error': 'PLAY requires --uid', 'status': controller.status()}
    if command != 'play' and uid is not None:
        return {'ok': False, 'error': 'UID only accepted with PLAY', 'status': controller.status()}
    if not (command in ('status', 'play', 'next', 'previous', 'stop', 'help')
            or re.fullmatch(r'select [0-9]{1,3}', command)):
        return {'ok': False, 'error': 'unknown command', 'status': controller.status()}
    try:
        if command == 'help':
            return {'ok': True, 'commands': COMMANDS, 'status': controller.status()}
        if command == 'play':
            controller.play(uid)
        elif command != 'status':
            controller.command(command)
        return {'ok': True, 'status': controller.status()}
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'status': controller.status()}


def serve(controller, socket_path, *, poll_seconds=0.5):
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
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        server.listen(4)
        server.setblocking(False)
        print(f'[M0.12a] local socket ready: {path} (0600)', flush=True)
        while True:
            controller.refresh()
            readable, _, _ = select.select([server], [], [], poll_seconds)
            if not readable:
                continue
            conn, _ = server.accept()
            with conn:
                conn.settimeout(2)
                data = bytearray()
                try:
                    while len(data) <= MAX_REQUEST:
                        chunk = conn.recv(min(128, MAX_REQUEST + 1 - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                        if b'\n' in chunk:
                            break
                    if len(data) > MAX_REQUEST or b'\n' not in data:
                        raise ValueError('request too long or unterminated')
                    request = json.loads(bytes(data).split(b'\n', 1)[0].decode('utf-8'))
                    reply = handle_command(controller, request)
                except (ValueError, UnicodeError, socket.timeout, OSError) as exc:
                    reply = {'ok': False, 'error': f'bad request: {type(exc).__name__}'}
                try:
                    conn.sendall((json.dumps(reply, ensure_ascii=False) + '\n').encode())
                except OSError:
                    pass
    finally:
        server.close()
        # Unlink only the socket that this instance created; no stale-socket takeover.
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
    try:
        device = detect_drive(base) if ns.device == 'auto' else ns.device
        state = prepare_selection(read_toc(device), 1)
        ctrl = Controller(state, None, None, ns.port,
                          ProcessServer(base, device, conf, ns.port), route=routed_source_ip)
        print('[M0.12a] ready; no Sonos action until explicit PLAY', flush=True)
        serve(ctrl, ns.socket)
    except KeyboardInterrupt:
        print('\n[M0.12a] shutdown; no Sonos STOP sent', flush=True)
    except Exception as exc:
        print(f'[M0.12a] service failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    finally:
        if ctrl:
            ctrl.close()
    return 0


if __name__ == '__main__':
    sys.exit(run())
