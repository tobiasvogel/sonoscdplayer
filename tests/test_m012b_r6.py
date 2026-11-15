"""M0.12b-r6: deterministic shutdown after live reads and replay generations."""
import sys
import threading
import unittest
from track_ring_http import Stream, Server


class TestResourceShutdown(unittest.TestCase):
    def test_close_stops_running_reader_and_diagnostics(self):
        # A synthetic infinite-ish reader, bounded by the shutdown timeout.
        cmd = [sys.executable, '-u', '-c',
               'import sys,time\nwhile True:\n sys.stdout.buffer.write(b"A"*4096); sys.stdout.buffer.flush(); time.sleep(.05)']
        stream = Stream(4_000_000, 65536, cmd, write_timeout=1)
        server = Server(('127.0.0.1', 0), stream, 1)
        stream.start()
        server.server_close()
        self.assertFalse(stream.thread.is_alive())
        self.assertFalse(stream._diag_thread.is_alive())
        # Closing again must be safe and must not start another reader.
        server.server_close()

    def test_close_releases_replay_generation_workers(self):
        cmd = [sys.executable, '-u', '-c',
               'import sys;sys.stdout.buffer.write(b"B"*240000)']
        stream = Stream(240000, 65536, cmd, write_timeout=2)
        server = Server(('127.0.0.1', 0), stream, 1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            stream.start()
            import http.client
            for _ in range(2):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=6)
                try:
                    conn.request('GET', '/track/1.wav')
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(len(response.read()), 240044)
                finally:
                    conn.close()
            self.assertEqual(server._generation, 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        for generation in server._streams:
            self.assertFalse(generation.thread.is_alive())
            self.assertFalse(generation._diag_thread.is_alive())
