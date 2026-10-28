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
    def __init__(self, base, device, config, port, *, popen=subprocess.Popen, startup_prebuffer=True, startup_timeout=45.0):
        self.base, self.device, self.config, self.port = base, device, config, port
        self.popen = popen
        self.startup_prebuffer = startup_prebuffer
        self.startup_timeout = startup_timeout
        self.proc = None
        self._reader_active = False
        self._reader_lock = threading.Lock()

    def reader_active(self):
        with self._reader_lock:
            return self._reader_active if self.alive() else False

    def _observe_line(self, line):
        with self._reader_lock:
            if 'READER started' in line:
                self._reader_active = True
            elif 'READER end written=' in line:
                self._reader_active = False

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
        with self._reader_lock:
            self._reader_active = True  # conservative until reader reports completion
        threading.Thread(target=stream_lines,
                         args=(p, events, time.monotonic(), self._observe_line), daemon=True).start()
        try:
            wait_reader(events, p, timeout=30)
            if self.startup_prebuffer:
                wait_startup_pcm(events, p, timeout=self.startup_timeout)
            if p.poll() is not None:
                raise RuntimeError('stream server exited at startup')
        except BaseException:
            self.close()
            raise

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def close(self):
        p, self.proc = self.proc, None
        with self._reader_lock:
            self._reader_active = False
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def wait_startup_pcm(events, proc, *, timeout):
    """Require real PCM before Sonos play_uri; never mistake process start for data."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError('stream server exited before PCM became ready')
        try:
            _, line = events.get(timeout=min(0.2, max(0.01, end - time.monotonic())))
        except queue.Empty:
            continue
        if line is None or 'READER end written=' in line:
            raise RuntimeError('CD reader ended before startup PCM became ready')
        if 'STARTUP_PCM_READY bytes=' in line:
            return
    raise TimeoutError('No CD PCM before startup timeout; Sonos PLAY not sent')


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


def ownership_for_transport(player, uid, url, expected):
    """Read-only, strict identity/topology/URI/transport gate; no automatic adoption."""
    if player.uid != uid:
        raise RuntimeError('ownership denied: UID changed')
    group = player.group
    members = list(group.members)
    if len(members) != 1 or members[0].uid != uid or group.coordinator.uid != uid:
        raise RuntimeError('ownership denied: not solo coordinator')
    transport = player.get_current_transport_info()['current_transport_state']
    if transport != expected:
        raise RuntimeError(f'ownership denied: transport {transport}, expected {expected}')
    if player.get_current_track_info().get('uri') != url:
        raise RuntimeError('ownership denied: exact URI mismatch')



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
        self.start_monotonic = None
        self.stopped_since = None
        self.status_hook = None  # optional read-only snapshot publisher

    def _url(self, track):
        return make_url(self.host, self.port, track)

    def status(self):
        return (f'mode={self.state.mode.value} track={self.state.selected_track}/'
                f'{len(self.state.track_numbers)} sonos={self.last_transport} '
                f'owned={bool(self.owned_url)} server={self.server.alive()}')
    
    def status_snapshot(self):
        """Read cached state only: no CD, network, or Sonos status queries.

        ``owned`` reports a cached session association, not authorization for
        later destructive commands. Those must repeat live ownership checks.
        """
        active = self.server.alive()
        owned = bool(self.owned_url)
        return {
            'schema_version': 1,
            'player': {
                'mode': self.state.mode.value,
                'track': self.state.selected_track,
                'track_count': len(self.state.track_numbers),
                'error': self.state.error,
            },
            'sonos': {
                'target_uid': self.uid if owned else None,
                'transport': self.last_transport,
                'owned': owned,
            },
            'stream': {
                'active': active,
            },
        }

    def refresh(self):
        if self.state.mode not in (Mode.STARTING, Mode.PLAYING, Mode.PAUSED):
            return
        if not self.server.alive():
            self._fail('stream server exited while playing')
            return
        try:
            player = self.verify_existing()
            transport = player.get_current_transport_info()['current_transport_state']
            self.last_transport = transport
            if transport == 'PLAYING':
                self.stopped_since = None
                ownership_snapshot(player, self.uid, self.owned_url)
                self.seen_playing = True
                if self.state.mode == Mode.STARTING:
                    self.state = self.state.playback_confirmed()
                elif self.state.mode == Mode.PAUSED:
                    self.state = self.state.resumed()
                    print('[M0.11d] PLAYING confirmed', flush=True)
            elif transport == 'PAUSED_PLAYBACK' and self.state.mode in (Mode.PLAYING, Mode.PAUSED):
                ownership_for_transport(player, self.uid, self.owned_url, 'PAUSED_PLAYBACK')
                if self.state.mode == Mode.PLAYING:
                    self.state = self.state.paused()
            elif transport == 'STOPPED' and self.state.mode == Mode.STARTING and not self.seen_playing:
                # Sonos can abort a slow first GET without ever reaching PLAYING.
                # Require a grace period so a transient STOPPED does not abort startup.
                now = time.monotonic()
                if self.stopped_since is None:
                    self.stopped_since = now
                if (self.start_monotonic is not None and now - self.start_monotonic >= 20
                        and now - self.stopped_since >= 5):
                    self._fail('Sonos remained STOPPED without confirmed PLAYING; startup aborted')
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
            elif transport == 'TRANSITIONING':
                self.stopped_since = None
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
        # Publish STARTING before the potentially long, synchronous PCM prebuffer.
        # No Sonos command has been sent and ownership is not claimed here.
        self.state = self.state.start_requested()
        if self.status_hook is not None:
            self.status_hook()
        try:
            self.server.start(self.state.selected_track)
            player = self.verify(candidate)  # immediate second STOPPED + solo gate
            if self.route is not None and self.route(player.ip_address) != host:
                raise RuntimeError('route to target changed during startup')
            if not self.server.alive():
                raise RuntimeError('server exited before Sonos PLAY')
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
        self.start_monotonic = time.monotonic()
        self.stopped_since = None

    def pause(self):
        if self.state.mode != Mode.PLAYING or not self.seen_playing or not self.owned_url or not self.server.alive():
            raise ValueError('PAUSE requires confirmed owned PLAYING')
        player = self.verify_existing()
        ownership_for_transport(player, self.uid, self.owned_url, 'PLAYING')
        ownership_for_transport(player, self.uid, self.owned_url, 'PLAYING')
        player.pause()
        self.state = self.state.paused()
        self.last_transport = 'PAUSED_PLAYBACK'

    def resume(self):
        if self.state.mode != Mode.PAUSED or not self.seen_playing or not self.owned_url or not self.server.alive():
            raise ValueError('RESUME requires confirmed owned PAUSED')
        player = self.verify_existing()
        ownership_for_transport(player, self.uid, self.owned_url, 'PAUSED_PLAYBACK')
        ownership_for_transport(player, self.uid, self.owned_url, 'PAUSED_PLAYBACK')
        player.play()
        self.state = self.state.resumed()
        self.last_transport = 'PLAYING'

    def playpause(self, uid=None):
        if self.state.mode == Mode.STOPPED:
            return self.play(uid)
        if uid is not None and uid != self.uid:
            raise ValueError('PLAYPAUSE target UID mismatch')
        if self.state.mode == Mode.PLAYING:
            return self.pause()
        if self.state.mode == Mode.PAUSED:
            return self.resume()
        raise ValueError('PLAYPAUSE forbidden in current mode')

    def _stop_owned(self):
        if self.state.mode not in (Mode.PLAYING, Mode.PAUSED) or not self.seen_playing or not self.owned_url:
            raise ValueError('STOP/track switch requires confirmed owned playback')
        player = self.verify_existing()
        expected = 'PLAYING' if self.state.mode == Mode.PLAYING else 'PAUSED_PLAYBACK'
        ownership_for_transport(player, self.uid, self.owned_url, expected)
        ownership_for_transport(player, self.uid, self.owned_url, expected)
        player.stop()  # only destructive action
        self.state = self.state.natural_end()
        self.owned_url, self.player = None, None
        self.last_transport = 'STOPPED'
        self.seen_playing = False
        self.server.close()

    def stop(self):
        # If Sonos already stopped during STARTING, release ONLY our local session.
        # Never issue Sonos STOP on this recovery path.
        if self.state.mode == Mode.STARTING and self.owned_url and self.player:
            player = self.verify_existing()
            if player.uid != self.uid:
                raise RuntimeError('target UID changed')
            group = player.group
            members = list(group.members)
            if (len(members) != 1 or members[0].uid != self.uid
                    or group.coordinator.uid != self.uid):
                raise RuntimeError('target no longer solo; refusing local release')
            state = player.get_current_transport_info()['current_transport_state']
            self.last_transport = state
            if state != 'STOPPED':
                raise ValueError('startup recovery only when Sonos is already STOPPED')
            self.state = self.state.natural_end()
            self.owned_url, self.player = None, None
            self.seen_playing = False
            self.start_monotonic, self.stopped_since = None, None
            self.server.close()
            return
        self._stop_owned()

    def reset(self):
        """Clear an already locally released ERROR; never contact or command Sonos."""
        if self.state.mode != Mode.ERROR:
            raise ValueError('RESET requires ERROR')
        if self.server.alive() or self.owned_url or self.player is not None:
            raise RuntimeError('RESET refused: local playback still attached')
        self.state = self.state.reset_error()
        self.uid = None
        self.last_transport = 'UNKNOWN'
        self.seen_playing = False
        self.start_monotonic = self.stopped_since = None

    def force_stop(self, uid, *, discover=None):
        """Explicit emergency action: stop selected solo speaker without URI proof.

        Never affects a group. Never chooses a device implicitly. No retries.
        The operator intentionally overrides the normal owned-stream restriction.
        """
        from sonos_parallel_test import find_target
        if not uid:
            raise ValueError('force STOP requires explicit target UID')
        if self.owned_url and self.uid != uid:
            raise RuntimeError('another target has active local session; force STOP refused')
        target = (discover or find_target)(uid)
        for _ in range(2):
            if target.uid != uid:
                raise RuntimeError('force STOP UID mismatch')
            group = target.group
            members = list(group.members)
            if (len(members) != 1 or members[0].uid != uid
                    or group.coordinator.uid != uid):
                raise RuntimeError('force STOP blocked: target not a solo coordinator')
        # This action CAN stop unrelated playback on the explicitly selected solo
        # speaker. Caller must request --force; never use it automatically.
        target.stop()
        self.last_transport = 'STOPPED'
        if self.uid == uid and self.owned_url:
            self.server.close()
            self.owned_url, self.player = None, None
            self.seen_playing = False
            self.start_monotonic, self.stopped_since = None, None
            if self.state.mode in (Mode.STARTING, Mode.PLAYING):
                self.state = self.state.natural_end()
            elif self.state.mode == Mode.ERROR:
                self.state = self.state.reset_error()
        # _fail() already detached the original session. A successful, explicit
        # emergency STOP still permits recovery from that released ERROR state.
        if (self.state.mode == Mode.ERROR and not self.server.alive()
                and self.owned_url is None and self.player is None):
            self.reset()

    def change(self, direction):
        if direction not in ('next', 'previous'):
            raise ValueError('invalid direction')
        if self.state.mode == Mode.STOPPED:
            self.state = (self.state.next_track() if direction == 'next'
                          else self.state.previous_track())
            return
        if self.state.mode not in (Mode.PLAYING, Mode.PAUSED):
            raise ValueError('track switch requires STOPPED or confirmed owned playback')
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
        elif name == 'pause' and len(tokens) == 1:
            self.pause()
        elif name == 'resume' and len(tokens) == 1:
            self.resume()
        elif name == 'playpause' and len(tokens) == 1:
            self.playpause()
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

    def shutdown_owned(self, policy='stop_owned'):
        """Best-effort shutdown; NEVER stop Sonos without two fresh URI proofs.

        No assumption is made about STARTING, ERROR, a changed group, or a
        changed URI.  The server is closed by close() in the caller's finally.
        """
        if policy not in ('stop_owned', 'leave_playing'):
            raise ValueError('unknown shutdown policy')
        if policy == 'leave_playing':
            return {'action': 'left_playing', 'reason': 'configured policy'}
        if not self.owned_url or self.player is None:
            return {'action': 'nothing_to_stop'}
        if self.state.mode not in (Mode.PLAYING, Mode.PAUSED) or not self.seen_playing:
            return {'action': 'not_stopped', 'reason': 'playback never confirmed'}
        try:
            player = self.verify_existing()
            expected = 'PLAYING' if self.state.mode == Mode.PLAYING else 'PAUSED_PLAYBACK'
            ownership_for_transport(player, self.uid, self.owned_url, expected)
            ownership_for_transport(player, self.uid, self.owned_url, expected)
            player.stop()
        except Exception as exc:
            return {'action': 'not_stopped',
                    'reason': f'{type(exc).__name__}: {exc}'}
        self.last_transport = 'STOPPED'
        self.state = self.state.natural_end()
        self.owned_url, self.player = None, None
        self.seen_playing = False
        return {'action': 'stopped_owned'}

    @staticmethod
    def sonos_live_status(uid, *, discover=None):
        """Read-only inspection; never attaches or changes playback ownership."""
        from sonos_parallel_test import find_target
        if not isinstance(uid, str) or not uid:
            raise ValueError('explicit UID required')
        player = (discover or find_target)(uid)
        if player.uid != uid:
            raise RuntimeError('live status target UID mismatch')
        group = player.group
        members = list(group.members)
        is_solo = (len(members) == 1 and members[0].uid == uid
                   and group.coordinator.uid == uid)
        transport = player.get_current_transport_info()['current_transport_state']
        uri = player.get_current_track_info().get('uri')
        return {'target_uid': uid, 'transport': transport, 'solo': is_solo,
                'uri_present': bool(uri),
                'player_stream_uri': isinstance(uri, str) and '/track/' in uri and uri.endswith('.wav')}
 
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
