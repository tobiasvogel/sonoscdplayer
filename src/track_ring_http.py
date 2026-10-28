#!/usr/bin/env python3
"""Experimental RAM-only CD-DA track HTTP server (v0.11.0-alpha.1).

No disk spool, no Sonos controls. Missing replay history can require sequential CD reread; active-reader conflicts -> 409.
This server is NOT a seamless concurrent retry implementation.
"""
from __future__ import annotations
import argparse
import re
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import threading
import time
from urllib.parse import urlsplit
from cd_toc import read_toc
from drive_access import drive_lock
from ram_ring import PCMRing, StreamingConfig, BufferTimeout, BufferClosed, DataExpired

SECTOR = 2352
CHUNK = 2352 * 25  # 58,800 bytes, 1/3s PCM
PREBUFFER = 176400  # one second
RATE = 44100 * 4

def wav_header(n: int) -> bytes:
    import struct
    return (b'RIFF' + struct.pack('<I', n + 36) + b'WAVEfmt ' +
            struct.pack('<IHHIIHH', 16, 1, 2, 44100, RATE, 4, 16) +
            b'data' + struct.pack('<I', n))

class ProbeCache:
    """Bounded copy of the initial CD-DA PCM, budgeted against the ring size."""
    def __init__(self, size: int):
        if size < CHUNK or size % 4:
            raise ValueError('probe cache must hold one complete PCM chunk')
        self.data = bytearray(size)
        self.ready = False
        self.lock = threading.Lock()

    def capture(self, offset: int, block: bytes):
        if offset != 0 or len(block) < len(self.data):
            return
        with self.lock:
            if not self.ready:
                self.data[:] = block[:len(self.data)]
                self.ready = True

    def read(self):
        with self.lock:
            return bytes(self.data) if self.ready else None


class Stream:
    def __init__(self, expected: int, capacity: int, command: list[str], write_timeout: float = 30, initial_prebuffer_ms: int = 0, ring: PCMRing | None = None, start_offset: int = 0, probe_cache: ProbeCache | None = None):
        if expected < 1 or expected % 4 or expected > (2**32 - 37):
            raise ValueError('Invalid WAV PCM size')
        self.expected = expected
        self.probe_cache = probe_cache
        ring_capacity = capacity - (len(probe_cache.data) if probe_cache is not None else 0)
        if ring_capacity < (CHUNK if probe_cache is not None else 4):
            raise ValueError('PCM RAM budget too small for probe cache')
        self.ring = ring if ring is not None else PCMRing(ring_capacity)
        if self.ring.capacity != ring_capacity:
            raise ValueError('reused ring capacity mismatch')
        self.total_ram_budget = capacity
        if type(start_offset) is not int or not 0 <= start_offset < expected or start_offset % SECTOR:
            raise ValueError('start_offset must be a sector boundary inside track')
        self.start_offset = start_offset
        self.command = list(command)
        self.write_timeout = write_timeout
        if type(initial_prebuffer_ms) is not int or not 0 <= initial_prebuffer_ms <= 30000:
            raise ValueError("initial_prebuffer_ms must be 0..30000")
        # 0 preserves M0.9l behaviour. Never request more than the ring can hold.
        requested = (RATE * initial_prebuffer_ms // 1000) // 4 * 4
        self.initial_prebuffer_bytes = min(max(CHUNK, requested), capacity, expected)
        self.error = None
        self.written = start_offset
        self.started = time.monotonic()
        # Pin offset 0 until first GET attaches, even if CD reads fast.
        self.anchor = self.ring.register(start_offset)
        self.lock = threading.Lock()
        self._diag_lock = threading.Lock()
        self._http_clients: dict[int, dict] = {}
        self._diag_stop = threading.Event()
        self._cancel = threading.Event()
        self._proc_lock = threading.Lock()
        self._proc = None
        self._diag_thread = None
        self._startup_ready_sent = False
        self.thread = threading.Thread(target=self._produce, daemon=True, name='cd-reader')

    def log(self, msg):
        print(f'[+{time.monotonic()-self.started:8.3f}s] {msg}', flush=True)

    def start(self):
        self.thread.start()
        self._diag_thread = threading.Thread(target=self._diag_loop, daemon=True, name='pcm-diagnostics')
        self._diag_thread.start()
 
    def shutdown(self, *, timeout=4.0):
        """Bounded, idempotent teardown of the reader and diagnostic worker.

        Called when the HTTP server is closed, including after failed/aborted
        tests. Never issues a Sonos control command or writes PCM to disk.
        """
        self._diag_stop.set()
        if self.thread.ident is not None and self.thread.is_alive():
            self.cancel_orphan()
            self.thread.join(timeout=timeout)
        if self._diag_thread is not None and self._diag_thread.is_alive():
            self._diag_thread.join(timeout=min(timeout, 2.0))

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

    def attach(self, offset=0):
        with self.lock:
            reader = self.ring.register(offset)  # DataExpired if requested history recycled
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
        # A replay generation must acquire the same lock as motor keep-alive.
        # The reader owns it until its subprocess has fully exited.
        device = self.command[self.command.index('-d') + 1] if '-d' in self.command else None
        # Unit-test synthetic producers have no optical drive to coordinate.
        with (drive_lock(device) if device is not None else nullcontext()):
            self._produce_locked()

    def _produce_locked(self):
        proc = None
        err = None
        try:
            proc = subprocess.Popen(sector_replay_command(self.command, self.start_offset), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
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
                if self.probe_cache is not None:
                    self.probe_cache.capture(self.written, block)
                self.ring.write(block, timeout=self.write_timeout)
                self.written += len(block)
                if not self._startup_ready_sent and self.written - self.start_offset >= min(CHUNK, self.expected - self.start_offset):
                    self._startup_ready_sent = True
                    self.log(f'STARTUP_PCM_READY bytes={self.written - self.start_offset}')
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

def sector_replay_command(command: list[str], start_offset: int) -> list[str]:
    """Seek to a sector relative to the selected track; never write PCM to disk.

    A single bracketed start offset without '-' ends at the selected track end.
    A non-cdparanoia synthetic producer is kept unchanged for unit tests.
    """
    result = list(command)
    if start_offset == 0 or not result or result[0] != 'cdparanoia':
        return result
    if start_offset % SECTOR:
        raise ValueError('sector replay requires sector boundary')
    try:
        raw_index = result.index('-r') + 1
        number = int(result[raw_index].split('[')[0])
    except (ValueError, IndexError) as exc:
        raise ValueError('unsupported cdparanoia invocation') from exc
    sectors = start_offset // SECTOR
    minutes, remaining = divmod(sectors, 75 * 60)
    seconds, frames = divmod(remaining, 75)
    result[raw_index] = f'{number}[{minutes}:{seconds:02d}.{frames:02d}]'
    return result


class ReplayBusy(Exception):
    """The optical drive is still in use by a previous generation."""


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _headers(self, status, length=None, ctype=None, *, content_range=None):
        self.send_response(status)
        if ctype: self.send_header('Content-Type', ctype)
        if length is not None: self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        if content_range is not None: self.send_header('Content-Range', content_range)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True

    def _request_span(self):
        """Return WAV byte start/end inclusive, or None after writing an error."""
        stream = self.server.stream
        size = stream.expected + 44
        stream.log(f'{self.command} received client={self.client_address[0]} '
                   f'path={self.path!r} range={self.headers.get("Range")!r} '
                   f'if_range={self.headers.get("If-Range")!r} '
                   f'available={stream.ring.stats["next_offset"]}')
        if urlsplit(self.path).path != self.server.route:
            self._headers(404, 0); return None
        if self.headers.get('If-Range') is not None:
            # We have no ETag or Last-Modified validator; do not misinterpret it.
            self._headers(400, 0); return None
        value = self.headers.get('Range')
        if value is None:
            return (0, size - 1, False)
        m = re.fullmatch(r'bytes=(\d+)-(\d*)', value.strip())
        if not m:
            self._headers(416, 0, content_range=f'bytes */{size}'); return None
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
        if start >= size or end < start:
            self._headers(416, 0, content_range=f'bytes */{size}'); return None
        return start, min(end, size - 1), True

    def do_HEAD(self):
        span = self._request_span()
        if span is None: return
        start, end, partial = span
        self._headers(206 if partial else 200, end - start + 1, 'audio/wav',
                      content_range=f'bytes {start}-{end}/{self.server.stream.expected + 44}' if partial else None)

    def do_GET(self):
        span = self._request_span()
        if span is None: return
        start, end, partial = span
        size = self.server.stream.expected + 44
        # A Sonos reconnect typically probes the beginning with a plain GET,
        # immediately disconnects after CHUNK, then issues a seeked Range GET.
        # Serve that probe from an explicitly budgeted RAM prefix without a CD
        # restart. This short-body response is Sonos-specific, not a complete
        # general-purpose HTTP 200 transfer; disabled after one such probe.
        if not partial and self.server.try_cached_probe(self):
            return
        # WAV starts with a 44-byte header; the ring offsets address PCM only.
        pcm_start = max(0, start - 44)
        aligned = pcm_start - pcm_start % 4
        try:
            stream, reader = self.server.acquire_get(aligned)
        except ReplayBusy:
            self.server.stream.log('GET reject: old generation still active; no concurrent CD reread')
            self._headers(409, 0)
            return
        sent = 0
        disconnected = False
        stream.http_begin(reader, self.client_address[0])
        try:
            header = wav_header(stream.expected)
            prefix = header[start:min(end + 1, 44)] if start < 44 else b''
            pcm_end = max(0, end + 1 - 44)
            pcm_remaining = max(0, pcm_end - pcm_start)
            if pcm_remaining:
                # Offset may be unaligned; consume at most three excess bytes
                # from the aligned ring cursor, then slice the response body.
                skip = pcm_start - aligned
                want = min(stream.initial_prebuffer_bytes, stream.expected - aligned)
                want = max(4, ((want + 3) // 4) * 4)
                stream.log(f'GET prebuffer target={want} bytes offset={aligned}')
                stream.ring.wait_ahead(reader, want, timeout=stream.write_timeout + 5)
                first = stream.ring.read(reader, min(CHUNK, stream.expected - aligned), timeout=stream.write_timeout + 5)
            if not first:
                self._headers(503, 0); return
                first = first[skip:skip + pcm_remaining]
            else:
                first = b''
            self._headers(206 if partial else 200, end - start + 1, 'audio/wav',
                          content_range=f'bytes {start}-{end}/{size}' if partial else None)
            if prefix: self.wfile.write(prefix)
            if first: self.wfile.write(first)
            sent = len(first)
            stream.http_sent(reader, sent)
            stream.log(f'GET first PCM sent={sent} range_start={start}')
            while sent < pcm_remaining:
                # The ring reader always consumes complete PCM frames.
                remaining = pcm_remaining - sent
                amount = min(CHUNK, ((remaining + 3)//4)*4)
                data = stream.ring.read(reader, amount, timeout=stream.write_timeout + 5)
                if not data: break
                data = data[:remaining]
                self.wfile.write(data)
                sent += len(data)
                stream.http_sent(reader, sent)
            stream.log(f'GET end sent={sent}/{pcm_remaining} wav_range={start}-{end}')
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
        self._streams = [stream]
        self._probe_used = False
        super().__init__(addr, Handler)

    def server_close(self):
        """Release all replay generations, not only the current one."""
        try:
            for stream in self._streams:
                stream.shutdown()
        finally:
            super().server_close()

    def try_cached_probe(self, handler):
        with self._generation_lock:
            stream = self.stream
            cache = stream.probe_cache
            if cache is None or self._probe_used:
                return False
            state = stream.ring.stats
            if state['oldest_offset'] == 0:
                return False
            prefix = cache.read()
            if prefix is None:
                return False
            self._probe_used = True
        # Do not lie about the WAV file's Content-Length. Sonos is known to
        # disconnect this probe after 58,800 PCM bytes; intentionally closing
        # it after that chunk is an experimental compatibility optimization.
        handler._headers(200, stream.expected + 44, 'audio/wav')
        try:
            handler.wfile.write(wav_header(stream.expected))
            handler.wfile.write(prefix)
            stream.log(f'GET cached-prefix probe sent={len(prefix)} no CD reread')
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            stream.log('GET cached-prefix probe disconnected')
        return True

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

    def acquire_get(self, offset=0):
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
                    return current, current.attach(offset)
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
            # Only resume from a sector boundary, preserving exact WAV byte
            # positions by starting the ring at the corresponding PCM offset.
            # A full GET always restarts at sector zero.
            seek_offset = (offset // SECTOR) * SECTOR if current.command and current.command[0] == 'cdparanoia' else 0
            current.ring.reset_for_replay(seek_offset)
            replacement = Stream(current.expected, current.total_ram_budget,
                                 list(current.command), current.write_timeout,
                                 current.initial_prebuffer_bytes * 1000 // RATE,
                                 ring=current.ring, start_offset=seek_offset, probe_cache=current.probe_cache)
            # Pin offset zero BEFORE starting cdparanoia, preventing start races.
            reader = replacement.attach(offset)
            self.stream = replacement
            self._streams.append(replacement)
            self._generation += 1
            self._probe_used = False
            replacement.log(f'REPLAY generation={self._generation} sector_seek_pcm={seek_offset} sequential CD reread')
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
    # Reserve 58,800 PCM bytes WITHIN the configured 16 MiB, not on top of it.
    stream = Stream(track.pcm_bytes, cfg.capacity_bytes, command, a.write_timeout,
                    a.initial_prebuffer_ms, probe_cache=ProbeCache(CHUNK))
    with Server((a.bind, a.port), stream, a.track) as server:
        print(f'Serving http://{a.bind}:{a.port}{server.route} '
              f'PCM={track.pcm_bytes} RAM={cfg.capacity_bytes} initial_prebuffer_ms={a.initial_prebuffer_ms}', flush=True)
        stream.start() # Start only AFTER server successfully binds.
        try: server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt: pass

if __name__ == '__main__': main()
