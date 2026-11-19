"""Metadata lifecycle: no optical drive, Sonos or network side effects."""
from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from sonoscdplayer.metadata.musicbrainz import DiscIdentity, LookupResult, ReleaseCandidate
from sonoscdplayer.metadata.worker import MetadataWorker


class ManualExecutor:
    def __init__(self):
        self.jobs = []
    def submit(self, function, *args):
        future = Future()
        self.jobs.append((future, function, args))
        return future
    def finish(self, index):
        future, function, args = self.jobs[index]
        future.set_result(function(*args))


class FakeToc:
    def __init__(self, label):
        self.label = label
        self.tracks = (type('Track', (), {'number': 1, 'duration_seconds': 100.0})(),)
    def __eq__(self, other):
        return isinstance(other, FakeToc) and self.label == other.label


class MetadataWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.executor = ManualExecutor()
        self.lookup = Mock(return_value=LookupResult('unique', (
            ReleaseCandidate('release', 'An Album', 'An Artist', None, None,
                             ('Track one',)),)))
        self.worker = MetadataWorker(
            cache_path=Path(self.tmp.name) / 'metadata.sqlite3',
            cover_dir=Path(self.tmp.name) / 'covers',
            display_supports_cover=False, executor=self.executor,
            identify=lambda toc: DiscIdentity('A' * 28, toc.label),
            lookup=self.lookup)

    def test_no_blocking_and_successful_release(self):
        toc = FakeToc('disc-one')
        self.worker.observe(toc)
        self.assertFalse(self.worker.poll(toc))
        self.executor.finish(0)
        self.assertTrue(self.worker.poll(toc))
        result = self.worker.snapshot
        self.assertEqual(result['match_status'], 'unique')
        self.assertEqual(result['album'], 'An Album')
        self.assertEqual(result['release_id'], 'release')
        self.assertIsNone(result['cover_file'])

    def test_cached_second_observation_skips_lookup(self):
        first = FakeToc('disc-one')
        self.worker.observe(first)
        self.executor.finish(0)
        self.worker.poll(first)
        self.worker.observe(None)
        self.worker.observe(first)
        self.executor.finish(1)
        self.assertTrue(self.worker.poll(first))
        self.assertEqual(self.worker.snapshot['match_status'], 'cached')
        self.assertEqual(self.lookup.call_count, 1)

    def test_old_disc_result_discarded(self):
        a, b = FakeToc('a'), FakeToc('b')
        self.worker.observe(a)
        self.worker.observe(b)
        self.executor.finish(0)
        self.assertFalse(self.worker.poll(b))  # Future for b is not done.
        self.executor.finish(1)
        self.assertTrue(self.worker.poll(b))
        self.assertEqual(self.worker.snapshot['disc_id'], 'A' * 28)

    def test_removed_disc_clears_snapshot(self):
        toc = FakeToc('a')
        self.worker.observe(toc)
        self.executor.finish(0)
        self.worker.poll(toc)
        self.assertTrue(self.worker.observe(None))
        self.assertIsNone(self.worker.snapshot)

    def test_duplicate_observation_not_requeued(self):
        toc = FakeToc('a')
        self.assertTrue(self.worker.observe(toc))
        self.assertFalse(self.worker.observe(FakeToc('a')))
        self.assertEqual(len(self.executor.jobs), 1)

    def test_network_failure_is_nonfatal(self):
        self.lookup.side_effect = TimeoutError()
        toc = FakeToc('a')
        self.worker.observe(toc)
        self.executor.finish(0)
        self.assertTrue(self.worker.poll(toc))
        self.assertEqual(self.worker.snapshot['match_status'], 'toc')
        self.assertEqual(self.worker.snapshot['lookup_status'], 'error')
        self.assertIsNotNone(self.worker._retry_at)


    def test_service_matching_policy_configuration(self):
        from sonoscdplayer.metadata.worker import matching_policy_from_config
        meta = {
            'auto_match': False,
            'matching': {'confidence_threshold': 97, 'minimum_margin': 25,
                         'minimum_track_titles': 4, 'rule_languages': ['fr']}
        }
        policy = matching_policy_from_config(meta)
        self.assertFalse(policy.auto_match)
        self.assertEqual(policy.confidence_threshold, 97)
        self.assertEqual(policy.minimum_margin, 25)
        self.assertEqual(policy.minimum_track_titles, 4)
        self.assertIn('fr', policy.language_rules.languages)

    def test_malformed_policy_rejected_without_player_failure(self):
        from sonoscdplayer.metadata.worker import matching_policy_from_config
        with self.assertRaises(ValueError):
            matching_policy_from_config({'matching': {'rule_languages': '../'}})


if __name__ == '__main__':
    unittest.main()
