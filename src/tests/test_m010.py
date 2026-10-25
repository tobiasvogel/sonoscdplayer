import http.client
import sys
import threading
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from track_ring_http import Stream, Server


def get(port):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=8)
    conn.request('GET', '/track/1.wav')
    res = conn.getresponse()
    status, data = res.status, res.read()
    conn.close()
    return status, data

class ReplayTests(unittest.TestCase):
    def test_expired_get_sequentially_rereads_source(self):
        count = 240000
        cmd = [sys.executable, '-c', f'import sys;sys.stdout.buffer.write(b"Z"*{count})']
        stream = Stream(count, 65536, cmd, write_timeout=4)
        server = Server(('127.0.0.1', 0), stream, 1)
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
        stream = Stream(count, 65536, cmd, write_timeout=4)
        server = Server(('127.0.0.1', 0), stream, 1)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start(); stream.start()
        try:
            self.assertEqual(get(server.server_port)[0], 200)
            # Keep a valid cursor alive even though the beginning expired.
            held = stream.ring.register(stream.ring.stats['oldest_offset'])
            self.assertEqual(get(server.server_port)[0], 409)
            self.assertEqual(server._generation, 1)
            stream.ring.unregister(held)
            self.assertEqual(get(server.server_port)[0], 200)
        finally:
            server.shutdown(); server.server_close(); th.join(2)

    def test_replay_uses_no_extra_track_storage_when_idle(self):
        count = 240000
        cmd = [sys.executable, '-c', f'import sys;sys.stdout.buffer.write(b"A"*{count})']
        stream = Stream(count, 65536, cmd, write_timeout=4)
        server = Server(('127.0.0.1', 0), stream, 1)
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

if __name__ == '__main__': unittest.main()
