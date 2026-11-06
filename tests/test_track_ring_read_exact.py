"""The CD pipe reader must tolerate short and frame-misaligned pipe reads."""
import sys
import unittest

import track_ring_http as t


class DribbleStream:
    """Raw-pipe stand-in: returns at most ``step`` bytes per read, then EOF."""

    def __init__(self, data, steps):
        self.data = data
        self.steps = list(steps)
        self.pos = 0
        self.calls = 0

    def read(self, size):
        step = self.steps[self.calls % len(self.steps)]
        self.calls += 1
        n = min(size, step, len(self.data) - self.pos)
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk


class ReadExactTests(unittest.TestCase):
    def test_reassembles_block_from_odd_sized_reads(self):
        data = bytes(range(256)) * 40
        stream = DribbleStream(data, [1, 3, 5, 4093, 2])
        self.assertEqual(t.read_exact(stream, len(data)), data)

    def test_returns_short_block_only_at_eof(self):
        stream = DribbleStream(b'abcdefgh', [3])
        self.assertEqual(t.read_exact(stream, 100), b'abcdefgh')
        self.assertEqual(t.read_exact(stream, 100), b'')

    def test_never_asks_for_more_than_remaining(self):
        sizes = []

        class Spy:
            def read(self, size):
                sizes.append(size)
                return b'\0' * min(size, 3)

        t.read_exact(Spy(), 8)
        self.assertEqual(sizes, [8, 5, 2])

    def test_unexpected_none_is_an_error(self):
        class NonBlocking:
            def read(self, size):
                return None

        with self.assertRaises(BlockingIOError):
            t.read_exact(NonBlocking(), 4)

    def test_cancellation_stops_between_partial_reads(self):
        stream = DribbleStream(b'x' * 100, [10])
        block = t.read_exact(stream, 100, cancelled=lambda: True)
        self.assertEqual(len(block), 10)


class ProducerTests(unittest.TestCase):
    def test_odd_sized_pipe_writes_do_not_abort_the_stream(self):
        # 8 flushed writes of 4093 bytes (not a multiple of 4); the total is.
        pieces, piece = 8, 4093
        count = pieces * piece
        self.assertEqual(count % 4, 0)
        script = ('import sys,time\n'
                  f'for _ in range({pieces}):\n'
                  f'    sys.stdout.buffer.write(b"\\x07"*{piece}); sys.stdout.buffer.flush()\n'
                  '    time.sleep(0.05)\n')
        stream = t.Stream(count, 1024 * 1024, [sys.executable, '-c', script], write_timeout=5)
        stream.start()
        stream.thread.join(15)
        self.assertFalse(stream.thread.is_alive())
        self.assertIsNone(stream.error)
        self.assertEqual(stream.written, count)


if __name__ == '__main__':
    unittest.main()
