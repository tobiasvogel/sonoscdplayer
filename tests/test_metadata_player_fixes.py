"""Review fixes for META-01 (17.11.): CD-TEXT in the player, covers optional,
one later lookup, cover download for Sonos."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from cover_preferences import CoverPreferences
from disc_musicbrainz import DiscIdentity, LookupResult, ReleaseCandidate
from player_metadata import MetadataWorker

from test_disc_state import CDS_DISC_OK, CDS_TRAY_OPEN, FakeController, make_monitor
from test_player_metadata import FakeToc, ManualExecutor

RELEASE = '12345678-1234-1234-1234-123456789abc'
CDTEXT = ('available', {'TITLE': 'Disc Title', 'PERFORMER': 'Disc Artist'},
          {1: {'TITLE': 'Text Track'}}, None)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.executor = ManualExecutor()
        self.clock = Clock()
        self.lookup = Mock(return_value=LookupResult('unique', (
            ReleaseCandidate(RELEASE, 'MB Album', 'MB Artist', None, None, ('MB Track',)),)))

    def worker(self, **kwargs):
        return MetadataWorker(
            cache_path=Path(self.tmp.name) / 'metadata.sqlite3',
            cover_dir=Path(self.tmp.name) / 'covers', executor=self.executor,
            identify=lambda toc: DiscIdentity('A' * 28, toc.label),
            lookup=self.lookup, clock=self.clock, **kwargs)

    def run_job(self, worker, toc, cdtext=None):
        worker.observe(toc, cdtext)
        self.executor.finish(len(self.executor.jobs) - 1)
        self.assertTrue(worker.poll(toc))
        return worker.snapshot

    def test_cdtext_wins_over_musicbrainz(self):
        snap = self.run_job(self.worker(), FakeToc('a'), CDTEXT)
        self.assertEqual((snap['album'], snap['artist'], snap['tracks'][0]['title']),
                         ('Disc Title', 'Disc Artist', 'Text Track'))
        self.assertEqual(snap['cdtext_status'], 'available')

    def test_cdtext_wins_over_cache(self):
        worker = self.worker()
        self.run_job(worker, FakeToc('a'))           # cached from MusicBrainz
        worker.observe(None)
        snap = self.run_job(worker, FakeToc('a'), CDTEXT)
        self.assertEqual((snap['match_status'], snap['album']), ('cached', 'Disc Title'))
        self.assertEqual(self.lookup.call_count, 1)

    def test_cover_failure_keeps_titles(self):
        worker = self.worker(sonos_uses_cover=True)
        worker._cover = SimpleNamespace(
            get_or_download=Mock(side_effect=RuntimeError('Pillow missing')))
        worker._cover_policy = CoverPreferences('on').download_policy(display_supports_cover=False)
        snap = self.run_job(worker, FakeToc('a'))
        self.assertEqual(snap['album'], 'MB Album')
        self.assertIsNone(snap['cover_file'])

    def test_one_later_lookup_when_offline(self):
        self.lookup.return_value = LookupResult('offline', error='x')
        worker = self.worker(retry_seconds=120)
        toc = FakeToc('a')
        snap = self.run_job(worker, toc)
        self.assertEqual((snap['album'], snap['lookup_status']), (None, 'offline'))
        self.assertFalse(worker.poll(toc))
        self.assertEqual(len(self.executor.jobs), 1)
        self.clock.now = 121
        self.lookup.return_value = LookupResult('unique', (
            ReleaseCandidate(RELEASE, 'MB Album', 'MB Artist', None, None, ('MB Track',)),))
        self.assertFalse(worker.poll(toc))           # resubmitted
        self.assertEqual(len(self.executor.jobs), 2)
        self.executor.finish(1)
        self.assertTrue(worker.poll(toc))
        self.assertEqual(worker.snapshot['album'], 'MB Album')
        self.lookup.return_value = LookupResult('offline', error='x')
        self.clock.now = 1000
        worker.poll(toc)
        self.assertEqual(len(self.executor.jobs), 2)  # never a loop

    def test_retry_is_dropped_with_the_disc(self):
        self.lookup.return_value = LookupResult('offline', error='x')
        worker = self.worker()
        toc = FakeToc('a')
        self.run_job(worker, toc)
        worker.observe(None)
        self.clock.now = 500
        self.assertFalse(worker.poll(None))
        self.assertEqual(len(self.executor.jobs), 1)


class CoverPolicyTests(unittest.TestCase):
    def test_auto_downloads_for_sonos_too(self):
        auto = CoverPreferences('auto')
        self.assertFalse(auto.download_policy(display_supports_cover=False).enabled)
        self.assertTrue(auto.download_policy(display_supports_cover=False,
                                             sonos_uses_cover=True).enabled)
        self.assertFalse(CoverPreferences('off').download_policy(
            display_supports_cover=True, sonos_uses_cover=True).enabled)
        with self.assertRaises(ValueError):
            auto.download_policy(display_supports_cover=False, sonos_uses_cover='yes')


class MonitorCdtextTests(unittest.TestCase):
    def test_cdtext_read_once_after_toc_and_cleared_on_eject(self):
        codes = [CDS_DISC_OK]
        disc = SimpleNamespace(tracks=[SimpleNamespace(number=1)])
        monitor, _calls = make_monitor(codes, disc)
        reads = []
        monitor.read_cdtext = lambda device: reads.append(device) or CDTEXT
        controller = FakeController()
        self.assertTrue(monitor.poll(controller))
        self.assertEqual(monitor.loaded_cdtext, CDTEXT)
        monitor.poll(controller)
        self.assertEqual(reads, ['/dev/sr0'])
        codes[0] = CDS_TRAY_OPEN
        monitor.poll(controller)
        self.assertIsNone(monitor.loaded_cdtext)

    def test_cdtext_failure_does_not_block_ready(self):
        disc = SimpleNamespace(tracks=[SimpleNamespace(number=1)])
        monitor, _calls = make_monitor([CDS_DISC_OK], disc)
        monitor.read_cdtext = Mock(side_effect=OSError('busy'))
        controller = FakeController()
        self.assertTrue(monitor.poll(controller))
        self.assertIsNone(monitor.loaded_cdtext)
        self.assertEqual(controller.disc_status.track_count, 1)


if __name__ == '__main__':
    unittest.main()
