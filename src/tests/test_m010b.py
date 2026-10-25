"""M0.10b orphan cancellation, no concurrent disc reread."""
import http.client
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from track_ring_http import Stream, Server


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
        self.stream = Stream(self.count, 65536, [sys.executable, '-u', '-c', script], write_timeout=3)
        self.server = Server(('127.0.0.1', 0), self.stream, 1)
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

    def test_disconnected_client_never_cancels_remaining_live_reader(self):
        held = self.stream.ring.register(0)
        try:
            self.server.release_get(self.stream, disconnected=True)
            self.assertFalse(self.stream._cancel.is_set())
            self.assertEqual(self.server._generation, 1)
        finally:
            self.stream.ring.unregister(held)

if __name__ == '__main__': unittest.main()
