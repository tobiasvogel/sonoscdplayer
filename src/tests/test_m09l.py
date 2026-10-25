import unittest
from ram_ring import PCMRing
from track_ring_http import Stream

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
        stream = Stream(16, 32, ['unused'])
        stream.http_begin(7, '127.0.0.1')
        stream.http_sent(7, 8)
        self.assertEqual(stream._http_clients[7]['sent'], 8)
        stream.http_end(7)
        self.assertEqual(stream._http_clients, {})

if __name__ == '__main__': unittest.main()
