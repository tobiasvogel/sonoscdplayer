"""Regression coverage for short Sonos HTTP disconnect/reconnect sequences.

No optical drive, Sonos device, network client, or PCM payload is required.
"""
import sys
from pathlib import Path
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from track_ring_http import Server


class FakeRing:
    def __init__(self):
        self.readers = 0
        self.finished = False
        self.oldest_offset = 0

    @property
    def stats(self):
        return {'readers': self.readers, 'finished': self.finished,
                'oldest_offset': self.oldest_offset}


class FakeThread:
    def is_alive(self):
        return True


class FakeStream:
    def __init__(self):
        self.ring = FakeRing()
        self.thread = FakeThread()
        self._cancel = threading.Event()
        self.error = None
        self.logs = []
        self.cancellations = 0

    def log(self, message):
        self.logs.append(message)

    def cancel_orphan(self):
        self.cancellations += 1
        self._cancel.set()

    def attach(self, offset):
        self.ring.readers += 1
        return self.ring.readers


class OrphanGraceTests(unittest.TestCase):
    def setUp(self):
        self.stream = FakeStream()
        self.server = Server(('127.0.0.1', 0), self.stream, 2)
        self.server.ORPHAN_GRACE_SECONDS = 0.12

    def tearDown(self):
        # Do not invoke Server.server_close(): FakeStream is not a real producer.
        with self.server._generation_lock:
            self.server._clear_orphan_timer()
        self.server.socket.close()

    def test_quick_get_reconnect_keeps_same_reader_generation(self):
        self.server.release_get(self.stream, disconnected=True)
        self.assertEqual(self.stream.cancellations, 0)
        stream, reader = self.server.acquire_get(0)
        self.assertIs(stream, self.stream)
        self.assertEqual(reader, 1)
        self.assertEqual(self.server._generation, 1)
        time.sleep(0.2)
        self.assertEqual(self.stream.cancellations, 0)

    def test_orphan_is_cancelled_after_bounded_grace(self):
        self.server.release_get(self.stream, disconnected=True)
        deadline = time.monotonic() + 2
        while not self.stream._cancel.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.stream._cancel.is_set())
        self.assertEqual(self.stream.cancellations, 1)

    def test_connected_other_reader_prevents_cancellation(self):
        self.stream.ring.readers = 1
        self.server.release_get(self.stream, disconnected=True)
        time.sleep(0.2)
        self.assertEqual(self.stream.cancellations, 0)

    def test_clean_get_does_not_schedule_orphan_cancel(self):
        self.server.release_get(self.stream, disconnected=False)
        time.sleep(0.2)
        self.assertEqual(self.stream.cancellations, 0)

    def test_stale_generation_is_not_cancelled(self):
        self.server.release_get(self.stream, disconnected=True)
        with self.server._generation_lock:
            self.server.stream = FakeStream()
        time.sleep(0.2)
        self.assertEqual(self.stream.cancellations, 0)


if __name__ == '__main__':
    unittest.main()
