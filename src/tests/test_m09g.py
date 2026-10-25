import http.client
import sys
import threading
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ram_ring import PCMRing, BufferTimeout
from track_ring_http import Stream, Server

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
        stream = Stream(count, 1024*1024, command, write_timeout=5)
        srv = Server(('127.0.0.1', 0), stream, 1)
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
        stream = Stream(400, 1024, [sys.executable, '-c', 'pass'])
        srv = Server(('127.0.0.1', 0), stream, 1)
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

if __name__ == '__main__': unittest.main()
