#!/usr/bin/env python3
"""M0.11c experimental one-shot track switch. Explicit owned-stream guard.

No grouping, no forced STOP on exit. STOP is issued ONLY for the previously
started exact HTTP URI on the exact solo coordinator, immediately rechecked.
"""
from __future__ import annotations
import argparse
import pathlib
import queue
import re
import subprocess
import sys
import threading
import time

from cd_toc import read_toc
from player_play import make_url, prepare_selection, verify_target
from player_state import Mode
from sonos_parallel_test import detect_drive, stream_lines, wait_reader, READER_DONE


def ownership_snapshot(player, expected_uid: str, expected_url: str) -> None:
    """Fail closed if transport/group/URI does not prove our own stream."""
    if player.uid != expected_uid:
        raise RuntimeError('ownership denied: target UID mismatch')
    group = player.group
    members = list(group.members)
    if len(members) != 1 or members[0].uid != expected_uid or group.coordinator.uid != expected_uid:
        raise RuntimeError('ownership denied: not a solo coordinator')
    transport = player.get_current_transport_info()['current_transport_state']
    if transport != 'PLAYING':
        raise RuntimeError(f'ownership denied: transport {transport}, expected PLAYING')
    info = player.get_current_track_info()
    uri = info.get('uri')
    if not isinstance(uri, str) or uri != expected_url:
        raise RuntimeError('ownership denied: current URI does not match launched stream')


def stopped_snapshot(player, expected_uid: str) -> None:
    """Recheck solo and STOPPED before initiating the new URL."""
    if player.uid != expected_uid:
        raise RuntimeError('target UID changed')
    group = player.group
    members = list(group.members)
    if len(members) != 1 or members[0].uid != expected_uid or group.coordinator.uid != expected_uid:
        raise RuntimeError('group changed after stop')
    state = player.get_current_transport_info()['current_transport_state']
    if state != 'STOPPED':
        raise RuntimeError(f'transport not STOPPED after switch: {state}')


def switch_owned(player, uid: str, old_url: str, new_url: str, *, stop=None, play=None):
    """The only path that can send a Sonos STOP. Fail closed on ambiguity."""
    ownership_snapshot(player, uid, old_url)
    # Fresh guard directly before the destructive action; any exception blocks it.
    ownership_snapshot(player, uid, old_url)
    (stop or player.stop)()
    stopped_snapshot(player, uid)
    # This is an advisory recheck, not a lock on external Sonos controllers.
    (play or player.play_uri)(new_url, title='SonosCDPlayer selected track')


def run(argv=None, *, verify=verify_target, drive=detect_drive, toc=read_toc,
        popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='auto')
    parser.add_argument('--track', type=int, required=True)
    parser.add_argument('--direction', required=True, choices=('next', 'previous'))
    parser.add_argument('--switch-after-seconds', type=int, default=25)
    parser.add_argument('--observe-seconds', type=int, default=210)
    parser.add_argument('--pi-host', required=True)
    parser.add_argument('--uid', required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--config', default='config.toml')
    args = parser.parse_args(argv)
    if not args.uid.strip() or not 15 <= args.switch_after_seconds <= 120 or not 20 <= args.observe_seconds <= 900:
        parser.error('Valid --uid, switch-after-seconds 15..120, observe-seconds 20..900 required')
    if args.device != 'auto' and not re.fullmatch(r'/dev/sr\d+', args.device):
        parser.error('Invalid device')
    try:
        old_url = make_url(args.pi_host, args.port, args.track)
    except ValueError as exc:
        parser.error(str(exc))
    base = pathlib.Path(__file__).resolve().parent
    proc = None
    events = queue.Queue()
    t0 = clock()

    def start_server(device, track, config):
        nonlocal proc, events
        events = queue.Queue()
        proc = popen([sys.executable, '-u', str(base/'track_ring_http.py'),
                      '--device', device, '--track', str(track), '--port', str(args.port),
                      '--config', str(config)], cwd=base, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=stream_lines, args=(proc, events, t0), daemon=True).start()
        wait_reader(events, proc, timeout=30)

    def end_server():
        nonlocal proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
        proc = None

    try:
        verify(args.uid)  # STOPPED guard before any first PLAY
        device = drive(base) if args.device == 'auto' else args.device
        disc = toc(device)
        state = prepare_selection(disc, args.track)
        after = state.next_track() if args.direction == 'next' else state.previous_track()
        if after.selected_track == state.selected_track:
            raise RuntimeError('Already at disc boundary; no track switch')
        new_url = make_url(args.pi_host, args.port, after.selected_track)
        config = pathlib.Path(args.config)
        if not config.is_absolute(): config = base/config
        if not config.is_file(): raise RuntimeError(f'Config missing: {config}')
        print(f'[M0.11c] track {state.selected_track} -> {after.selected_track}; initial PLAY', flush=True)
        start_server(device, state.selected_track, config)
        player = verify(args.uid)  # second STOPPED preflight
        if proc.poll() is not None: raise RuntimeError('Server ended before PLAY')
        if any(READER_DONE in item[1] for item in list(events.queue) if item[1]):
            raise RuntimeError('Reader finished prematurely')
        player.play_uri(old_url, title=f'SonosCDPlayer Track {state.selected_track}')
        until = clock() + args.switch_after_seconds
        seen_playing = False
        while clock() < until:
            transport = player.get_current_transport_info()['current_transport_state']
            if transport == 'PLAYING':
                # Do not equate unrelated PLAYING with ownership.
                ownership_snapshot(player, args.uid, old_url)
                seen_playing = True
            if proc.poll() is not None: raise RuntimeError('Streaming server terminated')
            sleep(min(2, max(0, until-clock())))
        if not seen_playing: raise RuntimeError('No confirmed owned PLAYING; switch blocked')
        print('[M0.11c] checking ownership for STOP', flush=True)
        # Phase 1: safely stop our old stream. The successor is only started after this.
        ownership_snapshot(player, args.uid, old_url)
        ownership_snapshot(player, args.uid, old_url)
        player.stop()
        print('[M0.11c] owned stream stopped; replacing HTTP server', flush=True)
        end_server()
        start_server(device, after.selected_track, config)
        player = verify(args.uid)  # strict STOPPED second preflight after new server
        if proc.poll() is not None: raise RuntimeError('New streaming server stopped')
        player.play_uri(new_url, title=f'SonosCDPlayer Track {after.selected_track}')
        deadline = clock() + args.observe_seconds
        confirmed = False
        while clock() < deadline:
            if proc.poll() is not None: raise RuntimeError('Streaming server terminated')
            transport = player.get_current_transport_info()['current_transport_state']
            if transport == 'PLAYING':
                ownership_snapshot(player, args.uid, new_url)
                confirmed = True
            print(f'[M0.11c] second track Sonos={transport}', flush=True)
            if confirmed and transport == 'STOPPED':
                print('[M0.11c] second track natural end; no extra STOP', flush=True)
                return 0
            sleep(min(5, max(0, deadline-clock())))
        if not confirmed: raise RuntimeError('Second track never reached owned PLAYING')
        print('[M0.11c] observation window ended; no extra STOP', flush=True)
        return 0
    except (RuntimeError, OSError, ValueError, TimeoutError, KeyError) as exc:
        print(f'[M0.11c] ABORT: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1
    finally:
        # Never issue Sonos STOP from cleanup.
        end_server()

if __name__ == '__main__':
    sys.exit(run())
