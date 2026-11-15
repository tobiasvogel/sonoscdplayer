"""M0.12c-r6: bounded prefix cache and one-time Sonos reconnect probe."""
import http.client
import threading
import unittest
from track_ring_http import CHUNK, ProbeCache, Server, Stream


class CachedProbeTests(unittest.TestCase):
    def test_cache_does_not_add_to_pcm_budget(self):
        cache = ProbeCache(CHUNK)
        budget = CHUNK * 5
        stream = Stream(CHUNK * 6, budget, ['synthetic'], probe_cache=cache)
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
        stream = Stream(CHUNK * 6, CHUNK * 4, ['synthetic'], probe_cache=cache)
        # Original front of track has expired, as after Sonos pause.
        stream.ring.unregister(stream.anchor)
        stream.anchor = None
        stream.ring.write(bytes([0x42]) * (CHUNK * 4))
        stream.ring.finish()
        self.assertGreater(stream.ring.stats['oldest_offset'], 0)
        server = Server(('127.0.0.1', 0), stream, 1)
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


if __name__ == '__main__':
    unittest.main()
