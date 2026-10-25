#!/usr/bin/env python3
"""v0.11.0-alpha.1: one-shot Sonos playback start while CD reader is active.

Safety: target by UID, refuse groups and non-STOPPED state; no stop/join/unjoin.
Test harness, not production control; does not modify the audio server.
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

SERVER_READY = re.compile(r'^Serving http://')
READER_STARTED = 'READER started'
READER_DONE = 'READER end'


def check_target(player, uid: str) -> str:
    """Raise before doing anything if group or transport is unsuitable."""
    if player.uid != uid:
        raise RuntimeError('UID des Zielgeräts stimmt nicht')
    group = player.group
    members = list(group.members)
    coordinator = group.coordinator
    state = coordinator.get_current_transport_info()['current_transport_state']
    print('Ziel:', player.player_name, 'UID:', player.uid, flush=True)
    print('Gruppe:', [(p.player_name, p.uid) for p in members], 'Koordinator:', coordinator.uid, flush=True)
    print('Transport vorher:', state, flush=True)
    if len(members) != 1 or members[0].uid != uid or coordinator.uid != uid:
        raise RuntimeError('ABBRUCH: keine Einzelgruppe mit korrektem Koordinator')
    if state != 'STOPPED':
        raise RuntimeError('ABBRUCH: Transport nicht STOPPED')
    return state


def find_target(uid: str):
    try:
        from soco import discover
    except ImportError as exc:
        raise RuntimeError('SoCo fehlt. Dieses Skript mit der bestehenden .venv starten') from exc
    devices = discover(timeout=5) or set()
    matches = [p for p in devices if p.uid == uid]
    if len(matches) != 1:
        raise RuntimeError(f'ABBRUCH: UID nicht eindeutig gefunden ({len(matches)} Treffer)')
    return matches[0]


def detect_drive(base: pathlib.Path) -> str:
    r = subprocess.run([sys.executable, str(base / 'sonos_cd_drive_probe.py')],
                       capture_output=True, text=True, timeout=90, check=False)
    print(r.stdout, end='', flush=True)
    if r.returncode != 0:
        raise RuntimeError('CD-Laufwerkerkennung fehlgeschlagen')
    matches = re.findall(r'^SELECTED_DEVICE=(/dev/sr\d+)$', r.stdout, re.M)
    if len(matches) != 1:
        raise RuntimeError('CD-Laufwerk nicht eindeutig')
    return matches[0]


def stream_lines(proc: subprocess.Popen, event_queue: queue.Queue, t0: float):
    try:
        for line in proc.stdout:
            txt = line.rstrip('\n')
            print(f'[server +{time.monotonic()-t0:7.3f}s] {txt}', flush=True)
            event_queue.put((time.monotonic(), txt))
    finally:
        event_queue.put((time.monotonic(), None))


def wait_reader(event_queue: queue.Queue, proc: subprocess.Popen, timeout: float):
    """Synchronize start to *actual* reader start, rather than arbitrary sleep."""
    end = time.monotonic() + timeout
    ready = False
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f'HTTP-Server beendet (Exit={proc.returncode})')
        try:
            _, line = event_queue.get(timeout=min(0.2, max(0.01, end-time.monotonic())))
        except queue.Empty:
            continue
        if line is None:
            raise RuntimeError('Serverausgabe unerwartet geschlossen')
        if READER_DONE in line:
            raise RuntimeError('Reader bereits fertig: paralleler Start nicht möglich')
        if SERVER_READY.match(line):
            ready = True
        if READER_STARTED in line:
            if not ready:
                raise RuntimeError('READER start vor HTTP-Bereitschaft')
            return
    raise TimeoutError('Timeout beim Warten auf den CD-Reader')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', default='auto', help='auto oder /dev/srN')
    p.add_argument('--track', type=int, default=1)
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--pi-host', required=True, help='vom Sonos erreichbare Raspberry-Pi-IP')
    p.add_argument('--config', default='config.toml')
    p.add_argument('--initial-prebuffer-ms', type=int, default=0)
    p.add_argument('--uid', required=True, help='exakte Ziel-Sonos-UID (keine automatische Zielwahl)')
    p.add_argument('--observe-seconds', type=int, default=210)
    a = p.parse_args()
    if not a.uid.strip() or not a.pi_host.strip():
        p.error('--uid und --pi-host dürfen nicht leer sein')
    if a.observe_seconds < 10 or a.observe_seconds > 900:
        p.error('--observe-seconds muss zwischen 10 und 900 liegen')
    if not (1 <= a.port <= 65535): p.error('Ungültiger Port')
    base = pathlib.Path(__file__).resolve().parent
    proc = None
    events = queue.Queue()
    t0 = time.monotonic()
    try:
        print('[test] Sicherheits-Vorprüfung', flush=True)
        player = find_target(a.uid)
        check_target(player, a.uid)
        device = detect_drive(base) if a.device == 'auto' else a.device
        print('[test] Gerät:', device, flush=True)
        argv = [sys.executable, '-u', str(base/'track_ring_http.py'),
                '--device', device, '--track', str(a.track), '--port', str(a.port),
                '--config', str((base/a.config).resolve() if not pathlib.Path(a.config).is_absolute() else a.config),
                '--initial-prebuffer-ms', str(a.initial_prebuffer_ms)]
        print('[test] Serverstart:', ' '.join(argv), flush=True)
        proc = subprocess.Popen(argv, cwd=base, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=stream_lines, args=(proc, events, t0), daemon=True).start()
        wait_reader(events, proc, timeout=30)
        print(f'[test +{time.monotonic()-t0:.3f}s] Reader gestartet; erneute Sicherheitsprüfung', flush=True)
        # Don't trust an old SoCo snapshot; fetch current group/transport again.
        player = find_target(a.uid)
        check_target(player, a.uid)
        if proc.poll() is not None:
            raise RuntimeError('Server vor Sonos-Befehl beendet')
        # Avoid accidentally passing tests where reader already completed.
        drained = []
        while True:
            try: drained.append(events.get_nowait()[1])
            except queue.Empty: break
        if any(x is not None and READER_DONE in x for x in drained):
            raise RuntimeError('CD bereits fertig; Test nicht parallel')
        url = f'http://{a.pi_host}:{a.port}/track/{a.track}.wav'
        print(f'[test +{time.monotonic()-t0:.3f}s] play_uri BEGIN url={url}', flush=True)
        start = time.monotonic()
        player.play_uri(url, title=f'SonosCDPlayer M0.9l Track {a.track}')
        print(f'[test +{time.monotonic()-t0:.3f}s] play_uri END duration={time.monotonic()-start:.3f}s', flush=True)
        print('[test] Weitere Serverlogs zeigen GET/READER end; Hörtest über die volle Trackdauer durchführen.', flush=True)
        until = time.monotonic() + a.observe_seconds
        while time.monotonic() < until:
            if proc.poll() is not None:
                print('[test] WARNUNG: HTTP-Server vorzeitig beendet', flush=True)
                return 2
            try:
                state = player.get_current_transport_info()['current_transport_state']
                print(f'[test +{time.monotonic()-t0:.3f}s] Sonos={state}', flush=True)
            except Exception as exc:
                print(f'[test] Statusabfrage fehlgeschlagen: {type(exc).__name__}: {exc}', flush=True)
            time.sleep(min(10, max(0, until - time.monotonic())))
        print('[test] Beobachtungszeit vorbei; Server wird beendet (keine Sonos-Stop-Aktion).', flush=True)
        return 0
    except (RuntimeError, TimeoutError, OSError) as exc:
        print(f'[test] {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()

if __name__ == '__main__':
    sys.exit(main())
