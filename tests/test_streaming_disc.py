"""Disc server: every track of one CD from one process, one reader at a time,
read-ahead of the next track (D-230). Synthetic PCM, no drive."""
import http.client
import sys
import threading
import time
import unittest

from ram_ring import MIN_BUFFER_MIB, StreamingConfig
from track_ring_http import DiscServer, ProbeCache, CHUNK, Stream

SIZES = {1: 240000, 2: 180000, 3: 120000}
FILL = {1: b'A', 2: b'B', 3: b'C'}


def command_for(track, *, delay=0.0):
    script = (f'import sys,time;time.sleep({delay});'
              f'sys.stdout.buffer.write({FILL[track]!r}*{SIZES[track]})')
    return [sys.executable, '-c', script]


def request(port, path, method='GET', headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    conn.request(method, path, headers=headers or {})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, data


class DiscServerTests(unittest.TestCase):
    def start(self, track=1, budget=3 * CHUNK, delay=0.0):
        stream = Stream(SIZES[track], budget, command_for(track, delay=delay), write_timeout=4,
                        probe_cache=ProbeCache(CHUNK))
        server = DiscServer(('127.0.0.1', 0), stream, track, tracks=SIZES,
                            command_for=lambda n: command_for(n, delay=delay))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stream.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        return server

    def test_serves_every_track_in_turn(self):
        server = self.start()
        for track in (1, 2, 3):
            status, data = request(server.server_port, f'/track/{track}.wav')
            self.assertEqual(status, 200)
            self.assertEqual(data[44:], FILL[track] * SIZES[track])
        self.assertEqual(server.track, 3)

    def test_jump_back_and_unknown_tracks(self):
        server = self.start()
        self.assertEqual(request(server.server_port, '/track/3.wav')[0], 200)
        status, data = request(server.server_port, '/track/1.wav')
        self.assertEqual((status, data[44:]), (200, FILL[1] * SIZES[1]))
        self.assertEqual(request(server.server_port, '/track/7.wav')[0], 404)
        self.assertEqual(request(server.server_port, '/track/x.wav')[0], 404)

    def test_next_track_is_read_ahead_after_full_delivery(self):
        server = self.start()
        self.assertEqual(request(server.server_port, '/track/1.wav')[0], 200)
        deadline = time.monotonic() + 5
        while server.track != 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(server.track, 2)                 # switched without a request
        deadline = time.monotonic() + 5
        while server.stream.ring.stats['next_offset'] == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertGreater(server.stream.ring.stats['next_offset'], 0)
        status, data = request(server.server_port, '/track/2.wav')
        self.assertEqual((status, data[44:]), (200, FILL[2] * SIZES[2]))

    def test_abandoned_request_does_not_block_the_wanted_track(self):
        """Sonos loads the queue's first entry, then seeks to the selected one
        (18.11.): the abandoned request must not hold the switch."""
        import socket
        server = self.start(track=3, delay=1.5)
        server.SWITCH_WAIT_SECONDS = 1.0
        abandoned = socket.create_connection(('127.0.0.1', server.server_port))
        abandoned.sendall(b'GET /track/1.wav HTTP/1.1\r\nHost: x\r\n\r\n')
        deadline = time.monotonic() + 5
        while server.track != 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(server.track, 1)
        abandoned.close()                       # Sonos moved on before any PCM
        status, data = request(server.server_port, '/track/3.wav')
        self.assertEqual((status, data[44:]), (200, FILL[3] * SIZES[3]))
        self.assertEqual(server.track, 3)

    def test_streaming_reader_is_not_preempted(self):
        server = self.start()
        stream = server.stream
        reader = stream.attach(0)
        stream.http_begin(reader, 'sonos')
        stream.http_sent(reader, CHUNK)
        self.assertEqual(stream.preempt_unstarted(), 0)
        self.assertIn(reader, stream.ring.reader_ids())
        stream.http_end(reader)
        stream.ring.unregister(reader)

    def test_head_of_another_track_does_not_switch(self):
        server = self.start()
        status, _ = request(server.server_port, '/track/3.wav', method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(server.track, 1)

    def test_switch_waits_while_another_client_reads(self):
        server = self.start()
        DiscServer.SWITCH_WAIT_SECONDS, saved = 0.3, DiscServer.SWITCH_WAIT_SECONDS
        self.addCleanup(setattr, DiscServer, 'SWITCH_WAIT_SECONDS', saved)
        held = server.stream.ring.register(0)           # a client still streaming track 1
        server.stream.http_begin(held, 'sonos')
        server.stream.http_sent(held, CHUNK)
        self.assertEqual(request(server.server_port, '/track/2.wav')[0], 409)
        self.assertEqual(server.track, 1)
        server.stream.http_end(held)
        server.stream.ring.unregister(held)
        status, data = request(server.server_port, '/track/2.wav')
        self.assertEqual((status, data[44:]), (200, FILL[2] * SIZES[2]))

    def test_one_ring_for_all_tracks(self):
        server = self.start()
        ring = server.stream.ring
        for track in (2, 3, 1):
            self.assertEqual(request(server.server_port, f'/track/{track}.wav')[0], 200)
            self.assertIs(server.stream.ring, ring)

    def test_switch_cancels_an_unwanted_read_ahead(self):
        server = self.start(delay=0.0)
        self.assertEqual(request(server.server_port, '/track/1.wav')[0], 200)
        status, data = request(server.server_port, '/track/3.wav')   # jump past read-ahead
        self.assertEqual((status, data[44:]), (200, FILL[3] * SIZES[3]))


class BudgetTests(unittest.TestCase):
    def test_minimum_budget(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'player.toml'
            path.write_text(f'[streaming]\nmax_buffer_mib = {MIN_BUFFER_MIB}\n')
            self.assertEqual(StreamingConfig.load(path).max_buffer_mib, 16)
            path.write_text('[streaming]\nmax_buffer_mib = 8\n')
            with self.assertRaises(ValueError):
                StreamingConfig.load(path)


if __name__ == '__main__':
    unittest.main()
