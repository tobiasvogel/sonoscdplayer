"""Talk to the track HTTP server process: its URLs and its startup output.

The player starts ``sonoscdplayer.audio.track_http_server`` as a child
process. That process reports its progress as lines on stdout; the player
reads them in a background thread and waits until the CD reader really runs
before it tells Sonos to play.
"""
from __future__ import annotations

import ipaddress
import queue
import re
import subprocess
import time

#: The server is listening (first line it prints).
SERVER_READY = re.compile(r'^Serving http://')
#: The CD reader started filling the RAM buffer.
READER_STARTED = 'READER started'
#: The CD reader finished the track.
READER_FINISHED = 'READER end'


def track_stream_path(track: int, *, session_id: str | None = None) -> str:
    """Build a track path shared by the URL builder and HTTP server.

    None preserves the standalone path; a session ID must be canonical hex.
    """
    if track < 1:
        raise ValueError('Invalid track')
    if session_id is None:
        return f'/track/{track}.wav'
    if not isinstance(session_id, str) or re.fullmatch(r'[0-9a-f]{32}', session_id) is None:
        raise ValueError('Invalid session ID')
    return f'/session/{session_id}/track/{track}.wav'


def track_stream_url(host: str, port: int, track: int, *,
                     session_id: str | None = None) -> str:
    """URL under which Sonos fetches a track from the player.

    Only a plain unicast IPv4 address is accepted as host. An optional
    canonical session ID distinguishes resources across server lifetimes.

    Raises:
        ValueError: invalid address, port, track number or session ID.
    """
    address = ipaddress.IPv4Address(host)
    if (address.is_multicast or address.is_unspecified or address.is_loopback
            or address.is_link_local):
        raise ValueError('pi-host must be a reachable unicast LAN IPv4 address')
    if not 1 <= port <= 65535 or track < 1:
        raise ValueError('Invalid port or track')
    return f'http://{address}:{port}{track_stream_path(track, session_id=session_id)}'


def forward_server_output(process: subprocess.Popen, lines: queue.Queue,
                          started_at: float, observer=None, startup_done=None) -> None:
    """Copy the server's output to the log and into ``lines``.

    Runs in a background thread until the process closes stdout. Every line
    goes into the queue as ``(monotonic time, text)``; ``(time, None)`` marks
    the end. ``observer`` is called with each line, if given. Once
    ``startup_done`` (a threading.Event) is set, nobody reads the queue any
    more, so lines are only logged (the queue would otherwise grow for the
    whole playback).
    """
    try:
        for line in process.stdout:
            text = line.rstrip('\n')
            print(f'[server +{time.monotonic() - started_at:7.3f}s] {text}', flush=True)
            if observer is not None:
                observer(text)
            if startup_done is None or not startup_done.is_set():
                lines.put((time.monotonic(), text))
    finally:
        lines.put((time.monotonic(), None))


def wait_for_reader_start(lines: queue.Queue, process: subprocess.Popen,
                          timeout: float) -> None:
    """Wait until the server listens and its CD reader has started.

    Raises:
        RuntimeError: the server exited, closed its output, or the reader
            started before the server listened or already finished.
        TimeoutError: nothing happened within ``timeout`` seconds.
    """
    deadline = time.monotonic() + timeout
    listening = False
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'track server exited (code {process.returncode})')
        try:
            _, line = lines.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            continue
        if line is None:
            raise RuntimeError('track server closed its output unexpectedly')
        if READER_FINISHED in line:
            raise RuntimeError('CD reader already finished')
        if SERVER_READY.match(line):
            listening = True
        if READER_STARTED in line:
            if not listening:
                raise RuntimeError('CD reader started before the server was listening')
            return
    raise TimeoutError('timed out waiting for the CD reader')
