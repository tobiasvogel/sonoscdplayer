"""Initial prebuffer must never exceed the PCM ring capacity."""
import unittest

import track_ring_http as t


class PrebufferClampTests(unittest.TestCase):
    def make(self, capacity, prebuffer_ms, with_probe=True):
        probe = t.ProbeCache(t.CHUNK) if with_probe else None
        return t.Stream(40_000_000, capacity, ['x'], initial_prebuffer_ms=prebuffer_ms,
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


if __name__ == '__main__':
    unittest.main()
