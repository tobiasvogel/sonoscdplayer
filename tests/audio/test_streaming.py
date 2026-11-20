"""CD audio streaming: RAM ring buffer, HTTP track server, byte ranges and reconnects (offline, synthetic PCM)."""
import http.client
import socket
import struct
import sys
import threading
import time
import unittest
from unittest.mock import patch
from sonoscdplayer.audio import track_http_server as t
from sonoscdplayer.audio.pcm_ring_buffer import PCMRing, BufferTimeout, DataExpired, BufferClosed
from sonoscdplayer.audio.track_http_server import (
    TrackStream,
    TrackServer,
    RATE,
    CHUNK,
    wav_header,
    sector_replay_command,
    SECTOR,
    ProbeCache,
)



class TestRing(unittest.TestCase):
    def test_backpressure_observed(self):
        ring = PCMRing(64)
        reader = ring.register(0)
        ring.write(b'1'*64)
        blocked = threading.Event()
        finished = threading.Event()
        def producer():
            blocked.set()
            ring.write(b'2'*32, timeout=2)
            finished.set()
        t = threading.Thread(target=producer)
        t.start(); self.assertTrue(blocked.wait(1)); time.sleep(.12)
        self.assertFalse(finished.is_set(), 'Producer should be blocked')
        self.assertEqual(ring.stats['retained_bytes'], 64)
        self.assertEqual(ring.read(reader, 32), b'1'*32)
        self.assertTrue(finished.wait(1))
        self.assertEqual(ring.stats['retained_bytes'], 64)
        self.assertEqual(ring.read(reader, 64), b'1'*32+b'2'*32)
        ring.unregister(reader); ring.finish(); t.join(1)

    def test_timeout_when_reader_stuck(self):
        ring = PCMRing(64); reader = ring.register(0); ring.write(b'a'*64)
        with self.assertRaises(BufferTimeout): ring.write(b'b'*4, timeout=.02)
        self.assertEqual(ring.stats['next_offset'], 64)
        ring.unregister(reader)


class TestHTTP(unittest.TestCase):
    def test_stream_full_and_expired_replay(self):
        # Synthetic producer, no CD hardware; 2.4 MB > 1 MiB bounded ring.
        count = 2400000
        command = [sys.executable, '-c', f'import sys;sys.stdout.buffer.write(b"\\x42"*{count})']
        stream = TrackStream(count, 1024*1024, command, write_timeout=5)
        srv = TrackServer(('127.0.0.1', 0), stream, 1)
        srv.timeout = .1
        t = threading.Thread(target=srv.serve_forever, daemon=True); t.start(); stream.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=8)
            conn.request('GET', '/track/1.wav')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            data = response.read()
            self.assertEqual(len(data), count+44)
            self.assertEqual(data[44:], b'B'*count)
            conn.close(); stream.thread.join(5)
            self.assertIsNone(stream.error)
            self.assertLessEqual(stream.ring.stats['retained_bytes'], stream.ring.capacity)
            self.assertGreater(stream.ring.stats['oldest_offset'], 0)
            conn = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=4)
            conn.request('GET', '/track/1.wav')
            r = conn.getresponse()
            self.assertEqual(r.status, 200)
            r.read(); conn.close()
        finally:
            srv.shutdown(); srv.server_close(); t.join(2)

    def test_head_without_consuming_anchor(self):
        stream = TrackStream(400, 1024, [sys.executable, '-c', 'pass'])
        srv = TrackServer(('127.0.0.1', 0), stream, 1)
        t=threading.Thread(target=srv.serve_forever, daemon=True);t.start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',srv.server_port)
            c.request('HEAD','/track/1.wav');r=c.getresponse()
            self.assertEqual(r.status,200)
            self.assertEqual(r.getheader('Content-Length'),'444')
            r.read();c.close()
            self.assertIsNotNone(stream.anchor)
        finally:
            srv.shutdown();srv.server_close();t.join(2)


class TestRetention(unittest.TestCase):
    def test_consumed_data_remains_until_space_required(self):
        ring = PCMRing(64)
        a = ring.register(0)
        ring.write(b'A' * 32)
        self.assertEqual(ring.read(a, 32), b'A' * 32)
        ring.unregister(a)
        self.assertEqual(ring.stats['oldest_offset'], 0)
        b = ring.register(0)
        self.assertEqual(ring.read(b, 32), b'A' * 32)
        ring.unregister(b)
        ring.write(b'B' * 32)
        self.assertEqual(ring.stats['oldest_offset'], 0)
        ring.write(b'C' * 16)
        self.assertEqual(ring.stats['oldest_offset'], 16)
        with self.assertRaises(DataExpired): ring.register(0)
        c = ring.register(16)
        self.assertEqual(ring.read(c, 64), b'A'*16+b'B'*32+b'C'*16)
        ring.unregister(c)

    def test_reader_prevents_eviction(self):
        ring = PCMRing(64)
        slow = ring.register(0)
        ring.write(b'A'*64)
        with self.assertRaises(Exception) as cm:
            ring.write(b'B'*4, timeout=.03)
        self.assertEqual(type(cm.exception).__name__, 'BufferTimeout')
        self.assertEqual(ring.stats['oldest_offset'], 0)
        ring.read(slow, 16)
        ring.write(b'B'*4, timeout=.1)
        self.assertEqual(ring.stats['oldest_offset'], 4)
        ring.unregister(slow)


class TestReplayHTTP(unittest.TestCase):
    def test_full_replay_when_track_fits_and_no_replay_when_not(self):
        for capacity, expected_second in ((1024 * 1024, 200), (65536, 200)):
            with self.subTest(capacity=capacity):
                count = 240000
                command = [sys.executable, '-c', f'import sys;sys.stdout.buffer.write(b"\\x42"*{count})']
                stream = TrackStream(count, capacity, command, write_timeout=4)
                srv = TrackServer(('127.0.0.1', 0), stream, 1)
                t = threading.Thread(target=srv.serve_forever, daemon=True)
                t.start()
                stream.start()
                try:
                    for idx in range(2):
                        status, body = get(srv.server_port)
                        self.assertEqual(status, 200 if idx == 0 else expected_second)
                        if idx == 0 or expected_second == 200:
                            self.assertEqual(body[44:], b'B'*count)
                    stream.thread.join(5)
                    srv.stream.thread.join(5)
                    self.assertIsNone(stream.error)
                    self.assertLessEqual(stream.ring.stats['retained_bytes'], capacity)
                    self.assertEqual(stream.ring.stats['oldest_offset'], 0 if capacity > count else count-capacity)
                finally:
                    srv.shutdown(); srv.server_close(); t.join(2)


class TestDiagnostics(unittest.TestCase):
    def test_ring_reports_bytes_and_wait_metrics(self):
        ring = PCMRing(16)
        rid = ring.register(0)
        ring.write(b'abcdefghijklmnop')
        d = ring.diagnostics
        self.assertEqual(d['next_offset'], 16)
        self.assertEqual(d['reader_offsets'][rid], 0)
        self.assertEqual(d['producer_wait_ms'], 0)
        self.assertEqual(d['consumer_wait_ms'], 0)
        self.assertEqual(ring.read(rid, 8), b'abcdefgh')
        self.assertEqual(ring.diagnostics['reader_offsets'][rid], 8)
        ring.unregister(rid)

    def test_http_progress_is_separate_from_reader_cursor(self):
        stream = TrackStream(16, 32, ['unused'])
        stream.http_begin(7, '127.0.0.1')
        stream.http_sent(7, 8)
        self.assertEqual(stream._http_clients[7]['sent'], 8)
        stream.http_end(7)
        self.assertEqual(stream._http_clients, {})


class PrebufferWaitTests(unittest.TestCase):
    def test_prebuffer_clamped_to_budget(self):
        s = TrackStream(3000000, 1048576, ['unused'], initial_prebuffer_ms=30000)
        self.assertEqual(s.initial_prebuffer_bytes, 1048576)
        self.assertEqual(TrackStream(3000000, 1048576, ['unused']).initial_prebuffer_bytes, CHUNK)
        self.assertEqual(TrackStream(3000000, 1048576, ['unused'], initial_prebuffer_ms=4000).initial_prebuffer_bytes, RATE * 4)

    def test_wait_ahead_preserves_cursor_and_wait_duration(self):
        ring = PCMRing(2048)
        rid = ring.register()
        def fill():
            time.sleep(0.03)
            ring.write(b'a' * 512)
        th = threading.Thread(target=fill)
        th.start()
        self.assertEqual(ring.wait_ahead(rid, 512, timeout=2), 512)
        th.join()
        d = ring.diagnostics
        self.assertEqual(d['reader_offsets'][rid], 0)
        self.assertGreater(d['consumer_wait_max_ms'], 10)
        self.assertLessEqual(d['next_offset'] - d['reader_offsets'][rid], d['capacity_bytes'])
        self.assertEqual(ring.read(rid, 512), b'a' * 512)
        ring.unregister(rid)

    def test_wait_timeout_and_failed_producer(self):
        ring = PCMRing(2048)
        rid = ring.register()
        with self.assertRaises(BufferTimeout):
            ring.wait_ahead(rid, 512, timeout=0.01)
        ring.finish('reader error')
        with self.assertRaises(BufferClosed):
            ring.wait_ahead(rid, 512, timeout=1)
        ring.unregister(rid)

    def test_invalid_prebuffer(self):
        for ms in [-1, 30001, 1.5]:
            with self.assertRaises(ValueError):
                TrackStream(3000000, 1048576, ['unused'], initial_prebuffer_ms=ms)


def get(port, *, wait_out_409=True):
    """GET the track; by default wait out the documented 409 that
    answers while the previous CD reader is still exiting, which takes a
    moment longer on a loaded machine."""
    deadline = time.monotonic() + 5
    while True:
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=8)
        conn.request('GET', '/track/1.wav')
        res = conn.getresponse()
        status, data = res.status, res.read()
        conn.close()
        if status != 409 or not wait_out_409 or time.monotonic() > deadline:
            return status, data
        time.sleep(0.05)


class ReplayTests(unittest.TestCase):
    def test_expired_get_sequentially_rereads_source(self):
        count = 240000
        cmd = [sys.executable, '-c', f'import sys;sys.stdout.buffer.write(b"Z"*{count})']
        stream = TrackStream(count, 65536, cmd, write_timeout=4)
        server = TrackServer(('127.0.0.1', 0), stream, 1)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start(); stream.start()
        try:
            for _ in range(3):
                status, content = get(server.server_port)
                self.assertEqual(status, 200)
                self.assertEqual(content[44:], b'Z'*count)
            self.assertEqual(server._generation, 3)
            self.assertNotEqual(server.stream, stream)
            self.assertLessEqual(server.stream.ring.stats['retained_bytes'], 65536)
        finally:
            server.shutdown(); server.server_close(); th.join(2)

    def test_refuses_reread_while_another_get_active(self):
        count = 240000
        cmd = [sys.executable, '-c', f'import sys;sys.stdout.buffer.write(b"X"*{count})']
        stream = TrackStream(count, 65536, cmd, write_timeout=4)
        server = TrackServer(('127.0.0.1', 0), stream, 1)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start(); stream.start()
        try:
            self.assertEqual(get(server.server_port)[0], 200)
            # Keep a valid cursor alive even though the beginning expired.
            held = stream.ring.register(stream.ring.stats['oldest_offset'])
            self.assertEqual(get(server.server_port, wait_out_409=False)[0], 409)
            self.assertEqual(server._generation, 1)
            stream.ring.unregister(held)
            self.assertEqual(get(server.server_port)[0], 200)
        finally:
            server.shutdown(); server.server_close(); th.join(2)

    def test_replay_uses_no_extra_track_storage_when_idle(self):
        count = 240000
        cmd = [sys.executable, '-c', f'import sys;sys.stdout.buffer.write(b"A"*{count})']
        stream = TrackStream(count, 65536, cmd, write_timeout=4)
        server = TrackServer(('127.0.0.1', 0), stream, 1)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start(); stream.start()
        try:
            self.assertEqual(get(server.server_port)[0], 200)
            old_ring = server.stream.ring
            self.assertEqual(get(server.server_port)[0], 200)
            self.assertIs(old_ring, server.stream.ring)
            self.assertEqual(server.stream.ring.capacity, 65536)
        finally:
            server.shutdown(); server.server_close(); th.join(2)


class OrphanTests(unittest.TestCase):
    def setUp(self):
        self.count = 58800 * 8
        # Slow segmented source: ensures the first TCP connection is still active
        # when we send RST, but it can finish normally for the subsequent GET.
        script = ('import sys,time\n'
                  'for n in range(8):\n'
                  ' sys.stdout.buffer.write(bytes([65+n])*58800)\n'
                  ' sys.stdout.buffer.flush()\n'
                  ' time.sleep(.18)\n')
        self.stream = TrackStream(self.count, 65536, [sys.executable, '-u', '-c', script], write_timeout=3)
        self.server = TrackServer(('127.0.0.1', 0), self.stream, 1)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.stream.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)
        # No child should survive a successful cancellation test.
        self.stream.cancel_orphan() if self.stream.thread.is_alive() else None
        self.stream.thread.join(timeout=3)
        if self.server.stream is not self.stream:
            new = self.server.stream
            if new.thread.is_alive(): new.cancel_orphan()
            new.thread.join(timeout=3)

    def test_aborted_socket_stops_orphan_then_restarts_from_zero(self):
        sock = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=4)
        sock.sendall(b'GET /track/1.wav HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        self.assertIn(b'200', sock.recv(512))
        # TCP RST (rather than FIN) makes the server's next write report failure.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        sock.close()
        deadline = time.monotonic() + 6
        while self.stream.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertFalse(self.stream.thread.is_alive(), 'orphan CD reader not cancelled')
        self.assertTrue(self.stream._cancel.is_set(), 'disconnect was not detected')
        c2 = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=6)
        c2.request('GET', '/track/1.wav')
        r2 = c2.getresponse()
        payload = r2.read()
        self.assertEqual(r2.status, 200)
        self.assertEqual(len(payload), self.count+44)
        self.assertEqual(payload[44:44+58800], b'A'*58800)
        self.assertEqual(self.server._generation, 2)
        self.assertIs(self.server.stream.ring, self.stream.ring)
        c2.close()

    def test_cancelled_ring_restarts_even_when_start_still_retained(self):
        # Regression: immediate Sonos reconnect with initial PCM still in RAM.
        # An old attach(0) would succeed, then fail on BufferClosed.
        original_reader = self.stream.attach()
        self.stream.ring.unregister(original_reader)
        self.stream.cancel_orphan()
        self.stream.thread.join(timeout=4)
        self.assertFalse(self.stream.thread.is_alive())
        self.assertEqual(self.stream.ring.stats['oldest_offset'], 0)
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=8)
        try:
            c.request('GET', '/track/1.wav')
            r = c.getresponse()
            payload = r.read()
            self.assertEqual(r.status, 200)
            self.assertEqual(len(payload), self.count + 44)
            self.assertEqual(self.server._generation, 2)
        finally:
            c.close()

    def test_disconnected_client_never_cancels_remaining_live_reader(self):
        held = self.stream.ring.register(0)
        try:
            self.server.release_get(self.stream, disconnected=True)
            self.assertFalse(self.stream._cancel.is_set())
            self.assertEqual(self.server._generation, 1)
        finally:
            self.stream.ring.unregister(held)


class TestResourceShutdown(unittest.TestCase):
    def test_close_stops_running_reader_and_diagnostics(self):
        # A synthetic infinite-ish reader, bounded by the shutdown timeout.
        cmd = [sys.executable, '-u', '-c',
               'import sys,time\nwhile True:\n sys.stdout.buffer.write(b"A"*4096); sys.stdout.buffer.flush(); time.sleep(.05)']
        stream = TrackStream(4_000_000, 65536, cmd, write_timeout=1)
        server = TrackServer(('127.0.0.1', 0), stream, 1)
        stream.start()
        server.server_close()
        self.assertFalse(stream.thread.is_alive())
        self.assertFalse(stream._diag_thread.is_alive())
        # Closing again must be safe and must not start another reader.
        server.server_close()

    def test_close_releases_replay_generation_workers(self):
        cmd = [sys.executable, '-u', '-c',
               'import sys;sys.stdout.buffer.write(b"B"*240000)']
        stream = TrackStream(240000, 65536, cmd, write_timeout=2)
        server = TrackServer(('127.0.0.1', 0), stream, 1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            stream.start()
            for _ in range(2):
                status, data = get(server.server_port)
                self.assertEqual(status, 200)
                self.assertEqual(len(data), 240044)
            self.assertEqual(server._generation, 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        for generation in server._streams:
            # close() signals the workers; give them a moment to finish.
            generation.thread.join(2)
            generation._diag_thread.join(2)
            self.assertFalse(generation.thread.is_alive())
            self.assertFalse(generation._diag_thread.is_alive())


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.expected = 58800 * 4
        self.pcm = bytes(range(256)) * (self.expected // 256) + bytes(range(self.expected % 256))
        script = 'import sys; n='+str(self.expected)+'; sys.stdout.buffer.write(bytes(range(256))*(n//256)+bytes(range(n%256)))'
        self.stream = TrackStream(self.expected, 65536, [sys.executable, '-u', '-c', script], write_timeout=3)
        self.server = TrackServer(('127.0.0.1', 0), self.stream, 1)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.stream.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
    def request(self, method='GET', headers=None):
        # Right after a complete read the old reader may still be finishing;
        # the server then answers the documented 409. Wait that out.
        deadline=time.monotonic()+5
        while True:
            c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=8)
            try:
                c.request(method,'/track/1.wav',headers=headers or {})
                r=c.getresponse(); code=r.status; hdr=dict(r.getheaders()); body=r.read()
            finally: c.close()
            if code!=409 or time.monotonic()>deadline:
                return code,hdr,body
            time.sleep(0.02)
    def test_middle_open_ended_range_replays_if_history_expired(self):
        # A complete first fetch forces most of the first 16-bit PCM segment out
        # of our small test ring. A resume must replay sequentially.
        code,_,full=self.request()
        self.assertEqual(code,200)
        start=56365
        code,hdr,body=self.request(headers={'Range':f'bytes={start}-'})
        self.assertEqual(code,206)
        self.assertEqual(hdr['Content-Range'],f'bytes {start}-{self.expected+43}/{self.expected+44}')
        self.assertEqual(body,full[start:])
        self.assertEqual(self.server._generation,2)
        self.assertIs(self.server.stream.ring,self.stream.ring)
    def test_range_in_wav_header_and_bounded_unaligned_pcm(self):
        code,hdr,body=self.request(headers={'Range':'bytes=0-42'})
        self.assertEqual((code,body),(206,wav_header(self.expected)[:43]))
        code,hdr,body=self.request(headers={'Range':'bytes=49-106'})
        self.assertEqual(code,206)
        self.assertEqual(body,self.pcm[5:63])
    def test_unsatisfiable_range(self):
        code,hdr,body=self.request(headers={'Range':'bytes=999999999-'})
        self.assertEqual(code,416)
        self.assertEqual(hdr['Content-Range'],f'bytes */{self.expected+44}')
        self.assertEqual(body,b'')
    def test_head_range_does_not_attach_reader(self):
        code,hdr,body=self.request('HEAD',{'Range':'bytes=44-47'})
        self.assertEqual(code,206)
        self.assertEqual(hdr['Content-Length'],'4')
        self.assertEqual(body,b'')


class SectorSeekTests(unittest.TestCase):
    def test_cdparanoia_span_sector_precision(self):
        command = ['cdparanoia', '-q', '-d', '/dev/srX', '-r', '2', '-']
        seeked = sector_replay_command(command, (75 * 61 + 37) * SECTOR)
        self.assertEqual(seeked, ['cdparanoia', '-q', '-d', '/dev/srX', '-r', '2[1:01.37]', '-'])
        self.assertEqual(command[-2], '2')

    def test_first_sector_no_seek(self):
        command = ['cdparanoia', '-r', '2', '-']
        self.assertEqual(sector_replay_command(command, 0), command)

    def test_unaligned_seek_is_rejected(self):
        with self.assertRaises(ValueError):
            sector_replay_command(['cdparanoia', '-r', '2', '-'], 4)

    def test_ring_replay_can_start_at_sector(self):
        ring = PCMRing(SECTOR * 4)
        ring.finish()
        ring.reset_for_replay(SECTOR * 100)
        rid = ring.register(SECTOR * 100)
        data = bytes(range(256)) * (SECTOR // 256) + bytes(range(SECTOR % 256))
        ring.write(data)
        self.assertEqual(ring.read(rid, SECTOR), data)
        ring.unregister(rid)

    def test_stream_start_offset_preserves_track_length(self):
        ring = PCMRing(SECTOR * 4)
        ring.finish()
        ring.reset_for_replay(SECTOR * 100)
        stream = TrackStream(SECTOR * 120, SECTOR * 4,
                        ['cdparanoia', '-r', '1', '-'], ring=ring, start_offset=SECTOR * 100)
        self.assertEqual(stream.written, SECTOR * 100)
        self.assertEqual(ring.stats['next_offset'], SECTOR * 100)
        stream.shutdown()

    def test_seek_is_applied_to_subprocess_not_base_command(self):
        expected = SECTOR * 102
        ring = PCMRing(SECTOR * 4)
        ring.finish()
        ring.reset_for_replay(SECTOR * 100)
        stream = TrackStream(expected, SECTOR * 4, ['cdparanoia', '-r', '1', '-'],
                        ring=ring, start_offset=SECTOR * 100)
        class Proc:
            def __init__(self):
                import io
                self.stdout = io.BytesIO(b'\0' * (2 * SECTOR))
            def poll(self): return 0
            def wait(self, **kwargs): return 0
        calls = []
        def fake_popen(command, **kwargs):
            calls.append(command)
            return Proc()
        with patch('sonoscdplayer.audio.track_http_server.subprocess.Popen', side_effect=fake_popen):
            stream._produce_locked()
        self.assertEqual(calls[0][-2], '1[0:01.25]')
        self.assertEqual(stream.command[-2], '1')
        self.assertEqual(stream.error, None)
        self.assertEqual(stream.written, expected)
        stream.shutdown()


class CachedProbeTests(unittest.TestCase):
    def test_cache_does_not_add_to_pcm_budget(self):
        cache = ProbeCache(CHUNK)
        budget = CHUNK * 5
        stream = TrackStream(CHUNK * 6, budget, ['synthetic'], probe_cache=cache)
        self.assertEqual(stream.ring.capacity + len(cache.data), budget)
        stream.shutdown()

    def test_first_pcm_chunk_captured_once(self):
        cache = ProbeCache(CHUNK)
        data = bytes([1]) * CHUNK
        cache.capture(0, data)
        cache.capture(0, bytes([2]) * CHUNK)
        self.assertEqual(cache.read(), data)
        cache.capture(CHUNK, bytes([3]) * CHUNK)
        self.assertEqual(cache.read(), data)

    def test_one_cached_probe_without_new_reader(self):
        cache = ProbeCache(CHUNK)
        cache.capture(0, bytes([0x41]) * CHUNK)
        stream = TrackStream(CHUNK * 6, CHUNK * 4, ['synthetic'], probe_cache=cache)
        # Original front of track has expired, as after Sonos pause.
        stream.ring.unregister(stream.anchor)
        stream.anchor = None
        stream.ring.write(bytes([0x42]) * (CHUNK * 4))
        stream.ring.finish()
        self.assertGreater(stream.ring.stats['oldest_offset'], 0)
        server = TrackServer(('127.0.0.1', 0), stream, 1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
            conn.request('GET', '/track/1.wav')
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            # Bypass content-length consistency check to inspect intentional
            # closed probe body (Sonos normally disconnects itself).
            body = resp.fp.read()
            self.assertEqual(len(body), 44 + CHUNK)
            self.assertEqual(body[44:], bytes([0x41]) * CHUNK)
            self.assertEqual(server._generation, 1)
            self.assertTrue(server._probe_used)
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class FakeRing:
    def __init__(self):
        self.readers = 0
        self.finished = False
        self.oldest_offset = 0

    @property
    def stats(self):
        return {'readers': self.readers, 'finished': self.finished,
                'oldest_offset': self.oldest_offset}


class FakeThread:
    def is_alive(self):
        return True


class FakeStream:
    def __init__(self):
        self.ring = FakeRing()
        self.thread = FakeThread()
        self._cancel = threading.Event()
        self.error = None
        self.logs = []
        self.cancellations = 0

    def log(self, message):
        self.logs.append(message)

    def cancel_orphan(self):
        self.cancellations += 1
        self._cancel.set()

    def attach(self, offset):
        self.ring.readers += 1
        return self.ring.readers


class OrphanGraceTests(unittest.TestCase):
    def setUp(self):
        self.stream = FakeStream()
        self.server = TrackServer(('127.0.0.1', 0), self.stream, 2)
        self.server.ORPHAN_GRACE_SECONDS = 0.12

    def tearDown(self):
        # Do not invoke TrackServer.server_close(): FakeStream is not a real producer.
        with self.server._generation_lock:
            self.server._clear_orphan_timer()
        self.server.socket.close()

    def test_quick_get_reconnect_keeps_same_reader_generation(self):
        self.server.release_get(self.stream, disconnected=True)
        self.assertEqual(self.stream.cancellations, 0)
        stream, reader = self.server.acquire_get(0)
        self.assertIs(stream, self.stream)
        self.assertEqual(reader, 1)
        self.assertEqual(self.server._generation, 1)
        time.sleep(0.2)
        self.assertEqual(self.stream.cancellations, 0)

    def test_orphan_is_cancelled_after_bounded_grace(self):
        self.server.release_get(self.stream, disconnected=True)
        deadline = time.monotonic() + 2
        while not self.stream._cancel.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.stream._cancel.is_set())
        self.assertEqual(self.stream.cancellations, 1)

    def test_connected_other_reader_prevents_cancellation(self):
        self.stream.ring.readers = 1
        self.server.release_get(self.stream, disconnected=True)
        time.sleep(0.2)
        self.assertEqual(self.stream.cancellations, 0)

    def test_clean_get_does_not_schedule_orphan_cancel(self):
        self.server.release_get(self.stream, disconnected=False)
        time.sleep(0.2)
        self.assertEqual(self.stream.cancellations, 0)

    def test_stale_generation_is_not_cancelled(self):
        self.server.release_get(self.stream, disconnected=True)
        with self.server._generation_lock:
            self.server.stream = FakeStream()
        time.sleep(0.2)
        self.assertEqual(self.stream.cancellations, 0)


class PrebufferClampTests(unittest.TestCase):
    def make(self, capacity, prebuffer_ms, with_probe=True):
        probe = t.ProbeCache(t.CHUNK) if with_probe else None
        return t.TrackStream(40_000_000, capacity, ['x'], initial_prebuffer_ms=prebuffer_ms,
                        probe_cache=probe)

    def test_prebuffer_fits_ring_when_probe_cache_reserves_budget(self):
        stream = self.make(1024 * 1024, 30000)
        self.assertLessEqual(stream.initial_prebuffer_bytes, stream.ring.capacity)

    def test_wait_ahead_accepts_clamped_prebuffer(self):
        stream = self.make(1024 * 1024, 30000)
        reader = stream.ring.register(0)
        # A timeout turns "prebuffer larger than the ring" into a fast failure
        # instead of blocking forever on a full, reader-pinned ring.
        stream.ring.write(b'\0' * stream.initial_prebuffer_bytes, timeout=0.5)
        available = stream.ring.wait_ahead(reader, stream.initial_prebuffer_bytes, timeout=0.5)
        self.assertGreaterEqual(available, stream.initial_prebuffer_bytes)

    def test_prebuffer_stays_frame_aligned(self):
        stream = self.make(1024 * 1024, 30000)
        self.assertEqual(stream.initial_prebuffer_bytes % 4, 0)

    def test_small_prebuffer_unchanged(self):
        stream = self.make(16 * 1024 * 1024, 1000)
        self.assertEqual(stream.initial_prebuffer_bytes, t.PREBUFFER)


class DribbleStream:
    """Raw-pipe stand-in: returns at most ``step`` bytes per read, then EOF."""

    def __init__(self, data, steps):
        self.data = data
        self.steps = list(steps)
        self.pos = 0
        self.calls = 0

    def read(self, size):
        step = self.steps[self.calls % len(self.steps)]
        self.calls += 1
        n = min(size, step, len(self.data) - self.pos)
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk


class ReadExactTests(unittest.TestCase):
    def test_reassembles_block_from_odd_sized_reads(self):
        data = bytes(range(256)) * 40
        stream = DribbleStream(data, [1, 3, 5, 4093, 2])
        self.assertEqual(t.read_exact(stream, len(data)), data)

    def test_returns_short_block_only_at_eof(self):
        stream = DribbleStream(b'abcdefgh', [3])
        self.assertEqual(t.read_exact(stream, 100), b'abcdefgh')
        self.assertEqual(t.read_exact(stream, 100), b'')

    def test_never_asks_for_more_than_remaining(self):
        sizes = []

        class Spy:
            def read(self, size):
                sizes.append(size)
                return b'\0' * min(size, 3)

        t.read_exact(Spy(), 8)
        self.assertEqual(sizes, [8, 5, 2])

    def test_unexpected_none_is_an_error(self):
        class NonBlocking:
            def read(self, size):
                return None

        with self.assertRaises(BlockingIOError):
            t.read_exact(NonBlocking(), 4)

    def test_cancellation_stops_between_partial_reads(self):
        stream = DribbleStream(b'x' * 100, [10])
        block = t.read_exact(stream, 100, cancelled=lambda: True)
        self.assertEqual(len(block), 10)


class ProducerTests(unittest.TestCase):
    def test_odd_sized_pipe_writes_do_not_abort_the_stream(self):
        # 8 flushed writes of 4093 bytes (not a multiple of 4); the total is.
        pieces, piece = 8, 4093
        count = pieces * piece
        self.assertEqual(count % 4, 0)
        script = ('import sys,time\n'
                  f'for _ in range({pieces}):\n'
                  f'    sys.stdout.buffer.write(b"\\x07"*{piece}); sys.stdout.buffer.flush()\n'
                  '    time.sleep(0.05)\n')
        stream = t.TrackStream(count, 1024 * 1024, [sys.executable, '-c', script], write_timeout=5)
        stream.start()
        stream.thread.join(15)
        self.assertFalse(stream.thread.is_alive())
        self.assertIsNone(stream.error)
        self.assertEqual(stream.written, count)


if __name__ == '__main__':
    unittest.main()
