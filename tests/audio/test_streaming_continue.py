"""Resume after a pause: data still in RAM is served from RAM and the CD is
read on from where the stopped reader ended, not from the track start."""
import http.client
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

from sonoscdplayer.audio import track_http_server as track_ring_http
from sonoscdplayer.audio.track_http_server import CHUNK, ProbeCache, Server, Stream

SIZE = 8 * CHUNK
SCRIPT = (
    'import sys,time\n'
    'size,start,slow_from=int(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3])\n'
    'out=sys.stdout.buffer\n'
    'for block in range(start,size,58800):\n'
    '    if slow_from and block>=slow_from:\n'
    '        time.sleep(0.5)\n'
    '    out.write(b"".join(bytes([f%251])*4 for f in range(block//4,min(size,block+58800)//4)))\n'
    '    out.flush()\n'
)


def expected(start=0, end=SIZE):
    return b''.join(bytes([frame % 251]) * 4 for frame in range(start // 4, end // 4))


class ContinueAfterPauseTests(unittest.TestCase):
    def setUp(self):
        self.commands = []

        def replay(command, start_offset):
            # The first reader becomes slow after 3 chunks; later readers are fast.
            pause_at = 3 * CHUNK if not self.commands else 0
            self.commands.append(start_offset)
            return [sys.executable, '-c', SCRIPT, str(SIZE), str(start_offset), str(pause_at)]
        patcher = patch.object(track_ring_http, 'sector_replay_command', replay)
        patcher.start()
        self.addCleanup(patcher.stop)
        stream = Stream(SIZE, 12 * CHUNK, ['cdparanoia', '-r', '1', '-'], write_timeout=4,
                        probe_cache=ProbeCache(CHUNK))
        self.server = Server(('127.0.0.1', 0), stream, 1)
        self.server.ORPHAN_GRACE_SECONDS = 0.2
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        stream.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join(2)))

    def test_resume_continues_from_ram_and_reads_on(self):
        # Sonos reads a bit, then drops the connection while paused.
        conn = socket.create_connection(('127.0.0.1', self.server.server_port))
        conn.sendall(b'GET /track/1.wav HTTP/1.1\r\nHost: x\r\n\r\n')
        conn.recv(CHUNK)
        deadline = time.monotonic() + 5
        while self.server.stream.ring.stats['next_offset'] < 3 * CHUNK and time.monotonic() < deadline:
            time.sleep(0.02)
        conn.close()
        deadline = time.monotonic() + 5            # orphan grace: reader stopped
        while not self.server.stream.ring.stats['finished'] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.server.stream.ring.stats['finished'])
        # Resume: the whole track again, as Sonos does.
        started = time.monotonic()
        conn2 = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        conn2.request('GET', '/track/1.wav')
        body = conn2.getresponse().read()
        conn2.close()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(body[44:], expected())
        self.assertEqual(len(self.commands), 2)
        self.assertEqual(self.commands[0], 0)
        self.assertGreaterEqual(self.commands[1], 3 * CHUNK)   # no reread from the start


if __name__ == '__main__':
    unittest.main()
