#!/usr/bin/env python3
"""M0.11d experimental persistent, single-process CLI CD controller.

All STOP actions demand two immediate ownership proofs. No group operations,
no autonomous Sonos STOP on exit. Never writes PCM to disk. POSIX terminal.
"""
from __future__ import annotations

import argparse
import pathlib
import queue
import re
import select
import socket
import subprocess
import sys
import threading
import time

from cd_toc import read_toc
from player_play import make_url, prepare_selection, verify_target
from player_state import Mode
from player_switch import ownership_snapshot
from sonos_parallel_test import detect_drive, stream_lines, wait_reader


class ProcessServer:
    def __init__(self, base, device, config, port, *, popen=subprocess.Popen):
        self.base, self.device, self.config, self.port = base, device, config, port
        self.popen = popen
        self.proc = None

    def start(self, track):
        if self.proc is not None:
            raise RuntimeError('server already attached')
        events = queue.Queue()
        p = self.popen([sys.executable, '-u', str(self.base / 'track_ring_http.py'),
                        '--device', self.device, '--track', str(track),
                        '--port', str(self.port), '--config', str(self.config)],
                       cwd=self.base, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.proc = p
        threading.Thread(target=stream_lines,
                         args=(p, events, time.monotonic()), daemon=True).start()
        try:
            wait_reader(events, p, timeout=30)
            if p.poll() is not None:
                raise RuntimeError('stream server exited at startup')
        except BaseException:
            self.close()
            raise

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def close(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def routed_source_ip(target_ip):
    """Local outbound IPv4 source chosen by kernel for the target speaker."""
    if not target_ip:
        raise RuntimeError('target has no IP address')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((target_ip, 1400))  # UDP connect does NOT send a packet
        result = sock.getsockname()[0]
    if not result or result.startswith('127.') or result == '0.0.0.0':
        raise RuntimeError('no LAN source address for target')
    return result


class Controller:
    """A single owner: only this instance's exact URI may be stopped."""
    def __init__(self, state, uid, host, port, server, verify=verify_target, route=None):
        self.state, self.uid = state, uid
        self.host, self.port = host, port
        self.server, self.verify = server, verify
        self.route = route  # None retains legacy explicit host behavior
        self.owned_url = None
        self.player = None
        self.last_transport = 'UNKNOWN'
        self.seen_playing = False

    def _url(self, track):
        return make_url(self.host, self.port, track)

    def status(self):
        return (f'mode={self.state.mode.value} track={self.state.selected_track}/'
                f'{len(self.state.track_numbers)} sonos={self.last_transport} '
                f'owned={bool(self.owned_url)} server={self.server.alive()}')

    def refresh(self):
        if self.state.mode not in (Mode.STARTING, Mode.PLAYING):
            return
        if not self.server.alive():
            self._fail('stream server exited while playing')
            return
        try:
            player = self.verify_existing()
            transport = player.get_current_transport_info()['current_transport_state']
            self.last_transport = transport
            if transport == 'PLAYING':
                ownership_snapshot(player, self.uid, self.owned_url)
                self.seen_playing = True
                if self.state.mode == Mode.STARTING:
                    self.state = self.state.playback_confirmed()
                    print('[M0.11d] PLAYING confirmed', flush=True)
            elif transport == 'STOPPED' and self.seen_playing:
                # Do not call this a natural end if the URI has changed.
                info = player.get_current_track_info()
                if info.get('uri') != self.owned_url:
                    self._fail('Sonos URI changed before STOPPED')
                    return
                self.state = self.state.natural_end()
                self.owned_url = None
                self.player = None
                self.server.close()
                print('[M0.11d] STOPPED observed; local server closed', flush=True)
            elif transport not in ('STOPPED', 'TRANSITIONING', 'PLAYING'):
                self._fail(f'unexpected Sonos transport state: {transport}')
        except Exception as exc:
            self._fail(f'ownership/status check failed: {type(exc).__name__}: {exc}')

    def verify_existing(self):
        if self.player is None:
            raise RuntimeError('no Sonos target attached')
        return self.player

    def _fail(self, message):
        self.state = self.state.failed(message)
        print(f'[M0.11d] ERROR: {message}; no Sonos STOP sent', flush=True)
        self.owned_url = None
        self.player = None
        self.server.close()

    def play(self, uid=None):
        if self.state.mode != Mode.STOPPED:
            raise ValueError('PLAY requires STOPPED')
        candidate = uid if uid is not None else self.uid
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError('PLAY requires target UID')
        target = self.verify(candidate)  # first safety gate before server spawn
        host = self.route(target.ip_address) if self.route is not None else self.host
        url = make_url(host, self.port, self.state.selected_track)
        self.server.close()
        self.server.start(self.state.selected_track)
        try:
            player = self.verify(candidate)  # immediate second STOPPED + solo gate
            if self.route is not None and self.route(player.ip_address) != host:
                raise RuntimeError('route to target changed during startup')
            if not self.server.alive():
                raise RuntimeError('server exited before Sonos PLAY')
            self.state = self.state.start_requested()
            player.play_uri(url, title=f'SonosCDPlayer Track {self.state.selected_track}')
        except BaseException as exc:
            self.server.close()
            self.owned_url, self.player = None, None
            self.state = self.state.failed(f'PLAY failed: {type(exc).__name__}: {exc}')
            raise
        self.uid, self.host = candidate, host
        self.player, self.owned_url = player, url
        self.seen_playing = False
        self.last_transport = 'TRANSITIONING'

    def _stop_owned(self):
        if self.state.mode != Mode.PLAYING or not self.seen_playing or not self.owned_url:
            raise ValueError('STOP/track switch requires confirmed owned PLAYING')
        player = self.verify_existing()
        ownership_snapshot(player, self.uid, self.owned_url)
        ownership_snapshot(player, self.uid, self.owned_url)
        player.stop()  # only destructive action
        self.state = self.state.natural_end()
        self.owned_url, self.player = None, None
        self.last_transport = 'STOPPED'
        self.seen_playing = False
        self.server.close()

    def stop(self):
        self._stop_owned()

    def change(self, direction):
        if direction not in ('next', 'previous'):
            raise ValueError('invalid direction')
        if self.state.mode == Mode.STOPPED:
            self.state = (self.state.next_track() if direction == 'next'
                          else self.state.previous_track())
            return
        if self.state.mode != Mode.PLAYING:
            raise ValueError('track switch requires STOPPED or confirmed PLAYING')
        old = self.state.selected_track
        numbers = self.state.track_numbers
        pos = numbers.index(old)
        index = min(pos + 1, len(numbers)-1) if direction == 'next' else max(pos - 1, 0)
        new = numbers[index]
        if new == old:
            print('[M0.11d] already at disc boundary', flush=True)
            return
        self._stop_owned()
        self.state = self.state.select(new)
        self.play()

    def command(self, line):
        tokens = line.strip().lower().split()
        if not tokens:
            return True
        name = tokens[0]
        if name in ('quit', 'exit') and len(tokens) == 1:
            return False
        if name == 'status' and len(tokens) == 1:
            print(self.status(), flush=True)
        elif name == 'play' and len(tokens) == 1:
            self.play()
        elif name == 'stop' and len(tokens) == 1:
            self.stop()
        elif name in ('next', 'previous') and len(tokens) == 1:
            self.change(name)
        elif name == 'select' and len(tokens) == 2 and tokens[1].isdigit():
            self.state = self.state.select(int(tokens[1]))
        elif name == 'help' and len(tokens) == 1:
            print('Commands: status, select N, play, next, previous, stop, quit', flush=True)
        else:
            raise ValueError('unknown command; type help')
        return True

    def close(self):
        # Crucial: leaving the CLI never causes a Sonos STOP.
        self.server.close()


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device', default='auto')
    ap.add_argument('--track', type=int, default=1)
    ap.add_argument('--pi-host', required=True)
    ap.add_argument('--uid', required=True)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--config', default='config.toml')
    ns = ap.parse_args(argv)
    if not ns.uid.strip():
        ap.error('nonempty --uid required')
    if ns.device != 'auto' and not re.fullmatch(r'/dev/sr\d+', ns.device):
        ap.error('--device must be auto or /dev/srN')
    try:
        make_url(ns.pi_host, ns.port, ns.track)
        base = pathlib.Path(__file__).resolve().parent
        conf = pathlib.Path(ns.config)
        if not conf.is_absolute():
            conf = base / conf
        if not conf.is_file():
            raise RuntimeError(f'missing config: {conf}')
        device = detect_drive(base) if ns.device == 'auto' else ns.device
        disc = read_toc(device)
        state = prepare_selection(disc, ns.track)
        server = ProcessServer(base, device, conf, ns.port)
        ctrl = Controller(state, ns.uid, ns.pi_host, ns.port, server)
    except Exception as exc:
        print(f'[M0.11d] setup failed: {exc}', file=sys.stderr)
        return 1
    print('[M0.11d] interactive; help lists commands. No Sonos actions until play.', flush=True)
    print(ctrl.status(), flush=True)
    try:
        while True:
            # Poll even while terminal is idle; no background Sonos control thread.
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 2)
                if ready:
                    line = sys.stdin.readline()
                    if not line:
                        break
                    try:
                        if not ctrl.command(line):
                            break
                        print(ctrl.status(), flush=True)
                    except Exception as exc:
                        print(f'[M0.11d] command rejected: {type(exc).__name__}: {exc}', flush=True)
                ctrl.refresh()
            except KeyboardInterrupt:
                print('\n[M0.11d] Ctrl-C: no Sonos STOP', flush=True)
                break
    finally:
        ctrl.close()
    return 0


if __name__ == '__main__':
    sys.exit(run())
