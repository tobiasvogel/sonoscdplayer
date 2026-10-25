#!/usr/bin/env python3
"""Experimental RAM-only CD-DA track HTTP server (v0.11.0-alpha.1).

No disk spool, no Sonos controls. Missing replay history can require sequential CD reread; active-reader conflicts -> 409.
This server is NOT a seamless concurrent retry implementation.
"""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import threading
import time
from urllib.parse import urlsplit
from cd_toc import read_toc
from ram_ring import PCMRing, StreamingConfig, BufferTimeout, BufferClosed, DataExpired

CHUNK = 2352 * 25  # 58,800 bytes, 1/3s PCM
PREBUFFER = 176400  # one second
RATE = 44100 * 4

def wav_header(n: int) -> bytes:
    import struct
    return (b'RIFF' + struct.pack('<I', n + 36) + b'WAVEfmt ' +
            struct.pack('<IHHIIHH', 16, 1, 2, 44100, RATE, 4, 16) +
            b'data' + struct.pack('<I', n))

class Stream:
    def __init__(self, expected: int, capacity: int, command: list[str], write_timeout: float = 30, initial_prebuffer_ms: int = 0, ring: PCMRing | None = None):
        if expected < 1 or expected % 4 or expected > (2**32 - 37):
            raise ValueError('Invalid WAV PCM size')
        self.expected = expected
        self.ring = ring if ring is not None else PCMRing(capacity)
        if self.ring.capacity != capacity:
            raise ValueError('reused ring capacity mismatch')
        self.command = command
        self.write_timeout = write_timeout
        if type(initial_prebuffer_ms) is not int or not 0 <= initial_prebuffer_ms <= 30000:
            raise ValueError("initial_prebuffer_ms must be 0..30000")
        # 0 preserves M0.9l behaviour. Never request more than the ring can hold.
        requested = (RATE * initial_prebuffer_ms // 1000) // 4 * 4
        self.initial_prebuffer_bytes = min(max(CHUNK, requested), capacity, expected)
        self.error = None
        self.written = 0
        self.started = time.monotonic()
        # Pin offset 0 until first GET attaches, even if CD reads fast.
        self.anchor = self.ring.register(0)
        self.lock = threading.Lock()
        self._diag_lock = threading.Lock()
        self._http_clients: dict[int, dict] = {}
        self._diag_stop = threading.Event()
        self._cancel = threading.Event()
        self._proc_lock = threading.Lock()
        self._proc = None
        self.thread = threading.Thread(target=self._produce, daemon=True, name='cd-reader')

    def log(self, msg):
        print(f'[+{time.monotonic()-self.started:8.3f}s] {msg}', flush=True)

    def start(self):
        self.thread.start()
        threading.Thread(target=self._diag_loop, daemon=True, name='pcm-diagnostics').start()

    def http_begin(self, reader_id: int, client: str):
        with self._diag_lock:
            self._http_clients[reader_id] = {'client': client, 'sent': 0}

    def http_sent(self, reader_id: int, sent: int):
        with self._diag_lock:
            if reader_id in self._http_clients:
                self._http_clients[reader_id]['sent'] = sent

    def http_end(self, reader_id: int):
        with self._diag_lock:
            self._http_clients.pop(reader_id, None)

    def _diag_loop(self):
        # Periodic output continues even while producer or HTTP thread waits.
        while not self._diag_stop.wait(1.0):
            d = self.ring.diagnostics
            with self._diag_lock:
                clients = [(rid, dict(info)) for rid, info in self._http_clients.items()]
            for rid, info in clients:
                # socket writes may be buffered; 'sent' is NOT Sonos's playback position.
                # One atomic ring snapshot: use the ring reader cursor, not a
                # separately sampled HTTP write counter. This is always <=
                # capacity for a live reader and does not include in-flight chunks.
                reader_offset = d['reader_offsets'].get(rid)
                if reader_offset is None:
                    continue
                ahead = max(0, d['next_offset'] - reader_offset)
                self.log(f'DIAG client={info["client"]} producer_offset={d["next_offset"]} '
                         f'http_sent={info["sent"]} reader_offset={reader_offset} available_ahead={ahead} '
                         f'ahead_s={ahead/RATE:.3f} '
                         f'retained={d["retained_bytes"]}/{d["capacity_bytes"]} '
                         f'producer_waits={d["producer_wait_count"]} '
                         f'producer_wait_ms={d["producer_wait_ms"]:.1f} '
                         f'consumer_waits={d["consumer_wait_count"]} '
                         f'consumer_wait_ms={d["consumer_wait_ms"]:.1f} '
                         f'consumer_wait_max_ms={d["consumer_wait_max_ms"]:.1f}')
            if d['finished'] and not clients:
                break

    def attach(self):
        with self.lock:
            reader = self.ring.register(0)  # DataExpired if offset 0 already recycled
            if self.anchor is not None:
                self.ring.unregister(self.anchor)
                self.anchor = None
            return reader

    def cancel_orphan(self):
        """Request cooperative reader shutdown; never start a second CD process here."""
        self._cancel.set()
        with self._proc_lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                proc.terminate()
        self.log('READER cancellation requested: disconnected orphan')

    def _produce(self):
        proc = None
        err = None
        try:
            proc = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            with self._proc_lock:
                self._proc = proc
                if self._cancel.is_set() and proc.poll() is None:
                    proc.terminate()
            self.log('READER started')
            while self.written < self.expected:
                if self._cancel.is_set():
                    raise IOError('reader cancelled after client disconnect')
                block = proc.stdout.read(min(CHUNK, self.expected - self.written))
                if not block:
                    if self._cancel.is_set():
                        raise IOError('reader cancelled after client disconnect')
                    raise IOError(f'Short PCM stream {self.written}/{self.expected}')
                if self._cancel.is_set():
                    raise IOError('reader cancelled after client disconnect')
                if len(block) % 4:
                    raise IOError('Unaligned PCM read')
                # Do not retry on BufferTimeout: write() may have partially committed block.
                self.ring.write(block, timeout=self.write_timeout)
                self.written += len(block)
            rc = proc.wait(timeout=15)
            if rc != 0:
                raise IOError(f'cdparanoia returned {rc}')
        except Exception as exc:
            err = f'{type(exc).__name__}: {exc}'
        finally:
            if proc:
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()
                if proc.stdout: proc.stdout.close()
            with self._proc_lock:
                self._proc = None
            self.error = err
            self.ring.finish(error=err)
            self.log(f'READER end written={self.written}/{self.expected} error={err}')

class ReplayBusy(Exception):
    """The optical drive is still in use by a previous generation."""


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def _headers(self, status, length=None, ctype=None):
        self.send_response(status)
        if ctype: self.send_header('Content-Type', ctype)
        if length is not None: self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'none')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True

    def _validate(self):
        stream = self.server.stream
        stream.log(f'{self.command} received client={self.client_address[0]} '
                   f'path={self.path!r} range={self.headers.get("Range")!r} '
                   f'if_range={self.headers.get("If-Range")!r} '
                   f'available={stream.ring.stats["next_offset"]}')
        if urlsplit(self.path).path != self.server.route:
            self._headers(404, 0); return False
        if self.headers.get('Range') is not None or self.headers.get('If-Range') is not None:
            # Explicit refusal rather than pretending to implement byte ranges.
            self._headers(400, 0); return False
        return True

    def do_HEAD(self):
        if self._validate():
            self._headers(200, self.server.stream.expected + 44, 'audio/wav')

    def do_GET(self):
        if not self._validate(): return
        try:
            stream, reader = self.server.acquire_get()
        except ReplayBusy:
            self.server.stream.log('GET reject: old generation still active; no concurrent CD reread')
            self._headers(409, 0)
            return
        sent = 0
        disconnected = False
        stream.http_begin(reader, self.client_address[0])
        try:
            # Wait for a real PCM block BEFORE sending 200, so producer failure
            # while waiting does not masquerade as a successful WAV stream.
            stream.log(f'GET prebuffer target={stream.initial_prebuffer_bytes} bytes')
            stream.ring.wait_ahead(reader, stream.initial_prebuffer_bytes, timeout=stream.write_timeout + 5)
            first = stream.ring.read(reader, min(CHUNK, stream.expected), timeout=stream.write_timeout + 5)
            if not first:
                self._headers(503, 0); return
            self._headers(200, stream.expected + 44, 'audio/wav')
            self.wfile.write(wav_header(stream.expected))
            self.wfile.write(first)
            sent += len(first)
            stream.http_sent(reader, sent)
            stream.log(f'GET first PCM sent={sent}')
            while sent < stream.expected:
                data = stream.ring.read(reader, min(CHUNK, stream.expected - sent), timeout=stream.write_timeout + 5)
                if not data: break
                self.wfile.write(data)
                sent += len(data)
                stream.http_sent(reader, sent)
            stream.log(f'GET end sent={sent}/{stream.expected}')
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            disconnected = True
            stream.log(f'GET disconnect sent={sent}: {type(exc).__name__}')
        except (BufferClosed, BufferTimeout, DataExpired) as exc:
            stream.log(f'GET stream error sent={sent}: {type(exc).__name__}: {exc}')
        finally:
            stream.http_end(reader)
            stream.ring.unregister(reader)
            self.server.release_get(stream, disconnected)

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, addr, stream, track):
        self.stream = stream
        self.route = f'/track/{track}.wav'
        self._generation_lock = threading.Lock()
        self._generation = 1
        super().__init__(addr, Handler)

    def release_get(self, stream: Stream, disconnected: bool):
        """Cancel a CD read ONLY when a broken HTTP connection left no readers.

        The generation lock serializes this decision with admissions, preventing
        a restart and a newly attached HTTP reader from racing the cancel.
        """
        if not disconnected:
            return
        with self._generation_lock:
            if stream is not self.stream:
                return
            if stream.ring.stats['readers'] != 0:
                stream.log('GET disconnect: other readers still attached; keep CD reader')
                return
            if stream.thread.is_alive():
                stream.cancel_orphan()

    def acquire_get(self):
        """Atomically attach a GET or schedule one SEQUENTIAL reread.

        Never start another optical reader while the existing producer is alive
        or any active HTTP cursor still exists. A HEAD never starts a reader.
        """
        with self._generation_lock:
            current = self.stream
            # A cancelled/failed producer may still have offset 0 retained.
            # Never attach a fresh GET to its terminally closed ring: a Sonos
            # reconnect would receive BufferClosed rather than a new stream.
            terminal_error = current._cancel.is_set() or (current.ring.stats['finished'] and current.error is not None)
            if not terminal_error:
                try:
                    return current, current.attach()
                except DataExpired:
                    pass
            # The old anchor exists only before the first GET; an expired
            # beginning implies it was removed. Count readers under ring lock.
            state = current.ring.stats
            if state['readers'] != 0:
                raise ReplayBusy()
            # The final HTTP bytes can be sent slightly before cdparanoia exits.
            # Allow a brief grace period for cleanup, but never overlap readers.
            current.thread.join(timeout=2.0)
            if current.thread.is_alive() or not current.ring.stats['finished']:
                raise ReplayBusy()
            # Stop old diagnostics and REUSE the same bytearray. No second PCM
            # allocation exists even transiently during an HTTP retry.
            current._diag_stop.set()
            current.ring.reset_for_replay()
            replacement = Stream(current.expected, current.ring.capacity,
                                 list(current.command), current.write_timeout,
                                 current.initial_prebuffer_bytes * 1000 // RATE,
                                 ring=current.ring)
            # Pin offset zero BEFORE starting cdparanoia, preventing start races.
            reader = replacement.attach()
            self.stream = replacement
            self._generation += 1
            replacement.log(f'REPLAY generation={self._generation} sequential CD reread')
            replacement.start()
            return replacement, reader

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', default='/dev/sr1')
    p.add_argument('--track', type=int, default=1)
    p.add_argument('--config', default='config.toml')
    p.add_argument('--bind', default='0.0.0.0')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--write-timeout', type=float, default=30)
    p.add_argument('--initial-prebuffer-ms', type=int, default=0, help='PCM-Vorlauf vor erstem WAV-Byte; 0 = bisheriger Start')
    a = p.parse_args()
    cfg = StreamingConfig.load(a.config)
    track = read_toc(a.device).get_track(a.track)
    command = ['cdparanoia', '-q', '-d', a.device, '-r', str(a.track), '-']
    stream = Stream(track.pcm_bytes, cfg.capacity_bytes, command, a.write_timeout, a.initial_prebuffer_ms)
    with Server((a.bind, a.port), stream, a.track) as server:
        print(f'Serving http://{a.bind}:{a.port}{server.route} '
              f'PCM={track.pcm_bytes} RAM={cfg.capacity_bytes} initial_prebuffer_ms={a.initial_prebuffer_ms}', flush=True)
        stream.start() # Start only AFTER server successfully binds.
        try: server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt: pass

if __name__ == '__main__': main()
