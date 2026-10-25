import http.client
import sys
import threading
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ram_ring import PCMRing, DataExpired
from track_ring_http import Stream, Server


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
                stream = Stream(count, capacity, command, write_timeout=4)
                srv = Server(('127.0.0.1', 0), stream, 1)
                t = threading.Thread(target=srv.serve_forever, daemon=True)
                t.start()
                stream.start()
                try:
                    for idx in range(2):
                        conn = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=8)
                        conn.request('GET', '/track/1.wav')
                        response = conn.getresponse()
                        self.assertEqual(response.status, 200 if idx == 0 else expected_second)
                        body = response.read()
                        if idx == 0 or expected_second == 200:
                            self.assertEqual(body[44:], b'B'*count)
                        conn.close()
                    stream.thread.join(5)
                    srv.stream.thread.join(5)
                    self.assertIsNone(stream.error)
                    self.assertLessEqual(stream.ring.stats['retained_bytes'], capacity)
                    self.assertEqual(stream.ring.stats['oldest_offset'], 0 if capacity > count else count-capacity)
                finally:
                    srv.shutdown(); srv.server_close(); t.join(2)


if __name__ == '__main__':
    unittest.main()
