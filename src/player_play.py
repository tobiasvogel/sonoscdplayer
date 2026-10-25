#!/usr/bin/env python3
"""M0.11b: one-shot selected-track PLAY; never issues STOP or alters Sonos groups.

This is an integration harness, not a persistent player daemon. The mode is
confirmed via Sonos transport polling; 'play_uri' alone is not confirmation.
"""
from __future__ import annotations
import argparse
import ipaddress
import pathlib
import queue
import subprocess
import sys
import threading
import time

from cd_toc import read_toc, TOCError
from player_state import PlayerState, Mode
from sonos_parallel_test import check_target, find_target, detect_drive, stream_lines, wait_reader, READER_DONE


def make_url(host: str, port: int, track: int) -> str:
    """Only explicit IPv4 hosts, so shell metacharacters/paths cannot enter URL."""
    ip = ipaddress.IPv4Address(host)
    if ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_link_local:
        raise ValueError('pi-host must be a reachable unicast LAN IPv4 address')
    if not 1 <= port <= 65535 or track < 1:
        raise ValueError('Invalid port or track')
    return f'http://{ip}:{port}/track/{track}.wav'


def prepare_selection(disc, selected: int) -> PlayerState:
    numbers = tuple(t.number for t in disc.tracks)
    state = PlayerState().disc_loaded(numbers).select(selected)
    # No unvalidated track number can reach a Sonos command.
    disc.get_track(state.selected_track)
    return state


def verify_target(uid: str):
    player = find_target(uid)
    check_target(player, uid)
    return player


def confirmed_mode(state: PlayerState, transport: str) -> PlayerState:
    if transport == 'PLAYING' and state.mode == Mode.STARTING:
        return state.playback_confirmed()
    if transport == 'STOPPED' and state.mode == Mode.PLAYING:
        return state.natural_end()
    return state


def run(argv=None, *, verify=verify_target, drive=detect_drive, toc=read_toc,
        popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='auto')
    parser.add_argument('--track', type=int, required=True)
    parser.add_argument('--pi-host', required=True)
    parser.add_argument('--uid', required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--config', default='config.toml')
    parser.add_argument('--observe-seconds', type=int, default=210)
    args = parser.parse_args(argv)
    if not args.uid.strip() or args.observe_seconds < 10 or args.observe_seconds > 900:
        parser.error('Nonempty --uid and --observe-seconds from 10 to 900 required')
    if args.device != 'auto' and not __import__('re').fullmatch(r'/dev/sr\d+', args.device):
        parser.error('--device must be auto or /dev/srN')
    try:
        url = make_url(args.pi_host, args.port, args.track)
    except ValueError as exc:
        parser.error(str(exc))
    base = pathlib.Path(__file__).resolve().parent
    proc = None
    t0 = clock()
    events = queue.Queue()
    state = PlayerState()
    try:
        print('[M0.11b] first Sonos safety preflight', flush=True)
        verify(args.uid)
        device = drive(base) if args.device == 'auto' else args.device
        disc = toc(device)
        state = prepare_selection(disc, args.track)
        print(f'[M0.11b] selected={state.selected_track} of {len(state.track_numbers)} '
              f'pcm_bytes={disc.get_track(state.selected_track).pcm_bytes} mode={state.mode.value}', flush=True)
        config = pathlib.Path(args.config)
        if not config.is_absolute():
            config = base / config
        if not config.is_file():
            raise RuntimeError(f'Config file not found: {config}')
        cmd = [sys.executable, '-u', str(base/'track_ring_http.py'), '--device', device,
               '--track', str(state.selected_track), '--port', str(args.port), '--config', str(config)]
        proc = popen(cmd, cwd=base, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     text=True, bufsize=1)
        threading.Thread(target=stream_lines, args=(proc, events, t0), daemon=True).start()
        wait_reader(events, proc, timeout=30)
        print('[M0.11b] second Sonos safety preflight', flush=True)
        player = verify(args.uid)
        if proc.poll() is not None:
            raise RuntimeError('Server ended before PLAY')
        # Catch a completed / failed reader before play_uri, preserving M0.9j guard.
        pending = []
        while True:
            try: pending.append(events.get_nowait()[1])
            except queue.Empty: break
        if any(line is not None and READER_DONE in line for line in pending):
            raise RuntimeError('CD reader ended before PLAY')
        state = state.start_requested()
        print(f'[M0.11b] mode={state.mode.value} PLAY selected track={state.selected_track}', flush=True)
        player.play_uri(url, title=f'SonosCDPlayer Track {state.selected_track}')
        until = clock() + args.observe_seconds
        was_playing = False
        while clock() < until:
            if proc.poll() is not None:
                raise RuntimeError('Audio server ended unexpectedly')
            # Transport state can lag; do not assume PLAYING after play_uri.
            try:
                transport = player.get_current_transport_info()['current_transport_state']
                state = confirmed_mode(state, transport)
                was_playing |= (state.mode == Mode.PLAYING)
                print(f'[M0.11b] Sonos={transport} player={state.mode.value}', flush=True)
                if was_playing and state.mode == Mode.STOPPED:
                    print('[M0.11b] natural end observed; no Sonos STOP sent', flush=True)
                    return 0
            except (OSError, KeyError) as exc:
                print(f'[M0.11b] transport status unavailable: {type(exc).__name__}', flush=True)
            sleep(min(5, max(0, until-clock())))
        if not was_playing:
            raise RuntimeError('Never observed Sonos PLAYING')
        print('[M0.11b] observation ended while playing; no Sonos STOP sent', flush=True)
        return 0
    except (RuntimeError, TOCError, OSError, ValueError, TimeoutError) as exc:
        state = state.failed(str(exc))
        print(f'[M0.11b] mode={state.mode.value}: {exc}', file=sys.stderr, flush=True)
        return 1
    finally:
        # Terminating a local HTTP server is NOT equivalent to stopping Sonos.
        # Only execute this harness when the observation window covers the track.
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()


if __name__ == '__main__':
    sys.exit(run())
