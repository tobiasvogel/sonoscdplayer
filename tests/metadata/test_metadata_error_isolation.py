"""Regression for nonfatal MusicBrainz/SQLite errors and guarded startup CD-TEXT."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from sonoscdplayer.metadata.musicbrainz import DiscIdentity, LookupResult, ReleaseCandidate
from sonoscdplayer.metadata.worker import MetadataWorker
from test_metadata_worker import FakeToc, ManualExecutor

RELEASE = '12345678-1234-1234-1234-123456789abc'
CDTEXT = ('available', {'TITLE': 'Local Album', 'PERFORMER': 'Local Artist'},
          {1: {'TITLE': 'Local Song'}}, None)


class FailureIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.executor = ManualExecutor()
        self.lookup = Mock(return_value=LookupResult('unique', (
            ReleaseCandidate(RELEASE, 'Online Album', 'Online Artist', None, None,
                             ('Online Song',)),)))
        self.worker = MetadataWorker(
            cache_path=Path(self.temp.name) / 'metadata.sqlite3',
            cover_dir=Path(self.temp.name) / 'covers',
            executor=self.executor,
            identify=lambda toc: DiscIdentity('A' * 28, toc.label),
            lookup=self.lookup, retry_delays=(120,))
        self.toc = FakeToc('test-disc')

    def finish(self):
        self.executor.finish(len(self.executor.jobs) - 1)
        self.assertTrue(self.worker.poll(self.toc))
        return self.worker.snapshot

    def test_musicbrainz_exception_preserves_cdtext_and_sets_retry(self):
        self.lookup.side_effect = RuntimeError('MusicBrainz down')
        self.worker.observe(self.toc, CDTEXT)
        result = self.finish()
        self.assertEqual(result['album'], 'Local Album')
        self.assertEqual(result['tracks'][0]['title'], 'Local Song')
        self.assertEqual(result['lookup_status'], 'error')
        self.assertIsNotNone(self.worker._retry_at)

    def test_sqlite_read_error_is_cache_miss(self):
        self.worker._cache = SimpleNamespace(
            get=Mock(side_effect=OSError('database unreadable')),
            put=Mock())
        self.worker.observe(self.toc, CDTEXT)
        result = self.finish()
        self.assertEqual(result['album'], 'Local Album')
        self.assertEqual(result['lookup_status'], 'unique')
        self.assertEqual(result['release_id'], RELEASE)
        self.worker._cache.put.assert_called_once()

    def test_sqlite_write_error_keeps_successful_online_metadata(self):
        self.worker._cache = SimpleNamespace(
            get=Mock(return_value=None),
            put=Mock(side_effect=PermissionError('read only database')))
        self.worker.observe(self.toc, CDTEXT)
        result = self.finish()
        self.assertEqual(result['album'], 'Local Album')
        self.assertEqual(result['release_id'], RELEASE)
        self.assertEqual(result['lookup_status'], 'unique')

    def test_cache_error_without_online_keeps_cdtext(self):
        self.worker._online = False
        self.worker._cache = SimpleNamespace(get=Mock(side_effect=ValueError('bad schema')))
        self.worker.observe(self.toc, CDTEXT)
        result = self.finish()
        self.assertEqual(result['album'], 'Local Album')
        self.lookup.assert_not_called()

    def test_bad_external_candidate_does_not_erase_local(self):
        self.lookup.return_value = LookupResult('unique', (
            ReleaseCandidate(RELEASE, 'Online Album', 'Online Artist', None, None,
                             ('Online Song',)),))
        self.worker.observe(self.toc, CDTEXT)
        with patch('sonoscdplayer.metadata.worker.merged_metadata', side_effect=ValueError('bad release')):
            result = self.finish()
        self.assertEqual(result['album'], 'Local Album')
        self.assertIsNone(result['release_id'])
        self.assertEqual(result['lookup_status'], 'error')


class BackgroundCdtextLockTests(unittest.TestCase):
    """The monitor reads CD-TEXT in the background, only with the drive lock."""

    def monitor(self, reader, lock):
        from sonoscdplayer.drive.disc_monitor import DiscMonitor
        monitor = DiscMonitor('/dev/sr0', None, lock=lock, cdtext=reader,
                              path_exists=lambda _: True, identity_reader=lambda _: None)
        return monitor

    def run_job(self, monitor):
        monitor.start_cdtext(SimpleNamespace(tracks=(1,)))
        monitor._cdtext_thread.join(7)
        return monitor.loaded_cdtext

    def test_reader_runs_only_with_drive_lock(self):
        events = []
        @contextmanager
        def lock(device, *, blocking):
            self.assertEqual(device, '/dev/sr0')
            self.assertFalse(blocking)
            events.append('locked')
            yield True
            events.append('released')
        def reader(device):
            events.append('reader')
            return CDTEXT
        self.assertEqual(self.run_job(self.monitor(reader, lock)), CDTEXT)
        self.assertEqual(events, ['locked', 'reader', 'released'])

    def test_busy_lock_never_reads_cdtext(self):
        @contextmanager
        def busy(_device, *, blocking):
            self.assertFalse(blocking)
            yield False
        reader = Mock(side_effect=AssertionError('drive touched while busy'))
        with patch('sonoscdplayer.drive.disc_monitor.time.sleep'), \
                patch('sonoscdplayer.drive.disc_monitor.time.monotonic',
                      side_effect=[0.0] + [10.0] * 10):
            self.assertIsNone(self.run_job(self.monitor(reader, busy)))
        reader.assert_not_called()

    def test_reader_error_does_not_prevent_ready(self):
        @contextmanager
        def acquired(_device, *, blocking):
            yield True
        self.assertIsNone(self.run_job(self.monitor(Mock(side_effect=OSError('drive error')),
                                                     acquired)))


if __name__ == '__main__':
    unittest.main()
