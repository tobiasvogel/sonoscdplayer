import threading
import time
import unittest
from ram_ring import PCMRing, BufferTimeout, BufferClosed
from track_ring_http import Stream, RATE, CHUNK

class TestM09m(unittest.TestCase):
    def test_prebuffer_clamped_to_budget(self):
        s = Stream(3000000, 1048576, ['unused'], initial_prebuffer_ms=30000)
        self.assertEqual(s.initial_prebuffer_bytes, 1048576)
        self.assertEqual(Stream(3000000, 1048576, ['unused']).initial_prebuffer_bytes, CHUNK)
        self.assertEqual(Stream(3000000, 1048576, ['unused'], initial_prebuffer_ms=4000).initial_prebuffer_bytes, RATE * 4)

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
                Stream(3000000, 1048576, ['unused'], initial_prebuffer_ms=ms)

if __name__ == '__main__':
    unittest.main()
