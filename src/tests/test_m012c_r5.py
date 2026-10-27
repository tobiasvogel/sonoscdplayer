"""M0.12c-r5: sector-aligned seek, bounded RAM, sequential replay semantics."""
import unittest
from unittest.mock import patch
from ram_ring import PCMRing
from track_ring_http import Stream, sector_replay_command, SECTOR


class SectorSeekTests(unittest.TestCase):
    def test_cdparanoia_span_sector_precision(self):
        command = ['cdparanoia', '-q', '-d', '/dev/srX', '-r', '2', '-']
        seeked = sector_replay_command(command, (75 * 61 + 37) * SECTOR)
        self.assertEqual(seeked, ['cdparanoia', '-q', '-d', '/dev/srX', '-r', '2[1:01.37]', '-'])
        self.assertEqual(command[-2], '2')

    def test_first_sector_no_seek(self):
        command = ['cdparanoia', '-r', '2', '-']
        self.assertEqual(sector_replay_command(command, 0), command)

    def test_unaligned_seek_is_rejected(self):
        with self.assertRaises(ValueError):
            sector_replay_command(['cdparanoia', '-r', '2', '-'], 4)

    def test_ring_replay_can_start_at_sector(self):
        ring = PCMRing(SECTOR * 4)
        ring.finish()
        ring.reset_for_replay(SECTOR * 100)
        rid = ring.register(SECTOR * 100)
        data = bytes(range(256)) * (SECTOR // 256) + bytes(range(SECTOR % 256))
        ring.write(data)
        self.assertEqual(ring.read(rid, SECTOR), data)
        ring.unregister(rid)

    def test_stream_start_offset_preserves_track_length(self):
        ring = PCMRing(SECTOR * 4)
        ring.finish()
        ring.reset_for_replay(SECTOR * 100)
        stream = Stream(SECTOR * 120, SECTOR * 4,
                        ['cdparanoia', '-r', '1', '-'], ring=ring, start_offset=SECTOR * 100)
        self.assertEqual(stream.written, SECTOR * 100)
        self.assertEqual(ring.stats['next_offset'], SECTOR * 100)
        stream.shutdown()

    def test_seek_is_applied_to_subprocess_not_base_command(self):
        expected = SECTOR * 102
        ring = PCMRing(SECTOR * 4)
        ring.finish()
        ring.reset_for_replay(SECTOR * 100)
        stream = Stream(expected, SECTOR * 4, ['cdparanoia', '-r', '1', '-'],
                        ring=ring, start_offset=SECTOR * 100)
        class Proc:
            def __init__(self):
                import io
                self.stdout = io.BytesIO(b'\0' * (2 * SECTOR))
            def poll(self): return 0
            def wait(self, **kwargs): return 0
        calls = []
        def fake_popen(command, **kwargs):
            calls.append(command)
            return Proc()
        with patch('track_ring_http.subprocess.Popen', side_effect=fake_popen):
            stream._produce_locked()
        self.assertEqual(calls[0][-2], '1[0:01.25]')
        self.assertEqual(stream.command[-2], '1')
        self.assertEqual(stream.error, None)
        self.assertEqual(stream.written, expected)
        stream.shutdown()

if __name__ == '__main__': unittest.main()
