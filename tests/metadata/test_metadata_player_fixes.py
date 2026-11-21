"""Disc metadata in the player: CD-TEXT in the player, covers optional,
one later lookup, cover download for Sonos."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from sonoscdplayer.metadata.cover_preferences import CoverPreferences
from sonoscdplayer.metadata.musicbrainz import DiscIdentity, LookupResult, ReleaseCandidate
from sonoscdplayer.metadata.worker import MetadataWorker

from disc_monitor_fakes import CDS_DISC_OK, CDS_TRAY_OPEN, FakeController, make_monitor
from test_metadata_worker import FakeToc, ManualExecutor

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

    def test_consensus_takes_the_cover_of_the_first_release_that_has_one(self):
        other = '22222222-2222-2222-2222-222222222222'
        self.lookup.return_value = LookupResult('ambiguous', (
            ReleaseCandidate(RELEASE, 'MB Album', 'MB Artist', None, None, ('MB Track',)),
            ReleaseCandidate(other, 'MB Album', 'MB Artist', None, None, ('MB track',))))
        worker = self.worker(sonos_uses_cover=True)
        worker._cover_policy = CoverPreferences('on').download_policy(display_supports_cover=False)
        cover = Path(self.tmp.name) / 'cover.jpg'
        worker._cover = SimpleNamespace(get_or_download=Mock(
            side_effect=lambda release, policy: cover if release == other else None))
        snap = self.run_job(worker, FakeToc('a'))
        self.assertEqual((snap['match_status'], snap['album'], snap['tracks'][0]['title']),
                         ('consensus', 'MB Album', 'MB Track'))
        self.assertEqual((snap['release_id'], snap['cover_file']), (other, str(cover)))

    def test_majority_without_album_name_takes_no_cover(self):
        """Audit META-002: titles agree, album names do not: no cover."""
        ids = [f'3333333{i}-3333-3333-3333-333333333333' for i in range(4)]
        albums = ('A', 'A', 'B', 'C')
        self.lookup.return_value = LookupResult('ambiguous', tuple(
            ReleaseCandidate(rid, album, 'MB Artist', None, None, ('Same',))
            for rid, album in zip(ids, albums)))
        worker = self.worker(sonos_uses_cover=True)
        worker._cover_policy = CoverPreferences('on').download_policy(display_supports_cover=False)
        fetch = Mock(return_value=Path(self.tmp.name) / 'cover.jpg')
        worker._cover = SimpleNamespace(get_or_download=fetch)
        snap = self.run_job(worker, FakeToc('a'))
        self.assertEqual((snap['match_status'], snap['album'], snap['tracks'][0]['title']),
                         ('majority', None, 'Same'))
        fetch.assert_not_called()
        self.assertIsNone(snap['cover_file'])

    def test_later_lookups_when_unavailable_then_stop(self):
        self.lookup.return_value = LookupResult('unavailable', error='MusicBrainz temporarily unavailable (HTTP 503)')
        worker = self.worker(retry_delays=(120, 600))
        toc = FakeToc('a')
        snap = self.run_job(worker, toc)
        self.assertEqual((snap['album'], snap['lookup_status']), (None, 'unavailable'))
        self.assertIn('HTTP 503', snap['lookup_error'])
        self.assertFalse(worker.poll(toc))
        self.assertEqual(len(self.executor.jobs), 1)
        self.clock.now = 121                         # first retry, still unavailable
        worker.poll(toc)
        self.executor.finish(1)
        self.assertTrue(worker.poll(toc))
        self.clock.now = 121 + 599
        worker.poll(toc)
        self.assertEqual(len(self.executor.jobs), 2)  # not yet
        self.clock.now = 121 + 601                   # second retry succeeds
        self.lookup.return_value = LookupResult('unique', (
            ReleaseCandidate(RELEASE, 'MB Album', 'MB Artist', None, None, ('MB Track',)),))
        worker.poll(toc)
        self.assertEqual(len(self.executor.jobs), 3)
        self.executor.finish(2)
        self.assertTrue(worker.poll(toc))
        self.assertEqual(worker.snapshot['album'], 'MB Album')
        self.lookup.return_value = LookupResult('offline', error='x')
        self.clock.now = 100000
        worker.poll(toc)
        self.assertEqual(len(self.executor.jobs), 3)  # never a loop

    def test_retry_is_dropped_with_the_disc(self):
        self.lookup.return_value = LookupResult('offline', error='x')
        worker = self.worker()
        toc = FakeToc('a')
        self.run_job(worker, toc)
        worker.observe(None)
        self.clock.now = 500
        self.assertFalse(worker.poll(None))
        self.assertEqual(len(self.executor.jobs), 1)


class DescribeTests(unittest.TestCase):
    def test_journal_line(self):
        from sonoscdplayer.metadata.worker import _cover_file, describe
        snap = {'disc_id': 'D', 'match_status': 'cached', 'cdtext_status': 'available',
                'lookup_status': None, 'release_id': RELEASE, 'cover_status': 'ok',
                'tracks': [{'title': 'a'}, {'title': None}]}
        line = describe(snap)
        self.assertIn('match=cached', line)
        self.assertIn('titles=1/2', line)
        self.assertIn('cover=ok', line)
        self.assertEqual(describe(None), '[metadata] none')
        policy = CoverPreferences('on').download_policy(display_supports_cover=False)
        off = CoverPreferences('off').download_policy(display_supports_cover=True)
        missing = SimpleNamespace(get_or_download=lambda *a, **k: None)
        broken = SimpleNamespace(get_or_download=Mock(side_effect=RuntimeError()))
        self.assertEqual(_cover_file(missing, None, policy), (None, 'no_release'))
        self.assertEqual(_cover_file(missing, RELEASE, off), (None, 'off'))
        self.assertEqual(_cover_file(missing, RELEASE, policy), (None, 'unavailable'))
        self.assertEqual(_cover_file(broken, RELEASE, policy), (None, 'failed:RuntimeError'))


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
        self.assertTrue(monitor.poll(controller))       # READY before CD-TEXT
        monitor._cdtext_thread.join(2)
        self.assertEqual(monitor.loaded_cdtext, CDTEXT)
        self.assertFalse(monitor.cdtext_pending)
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
        monitor._cdtext_thread.join(2)
        self.assertIsNone(monitor.loaded_cdtext)
        self.assertEqual(controller.disc_status.track_count, 1)

    def test_play_cancels_a_hanging_cdtext_read(self):
        import threading
        disc = SimpleNamespace(tracks=[SimpleNamespace(number=1)])
        monitor, _calls = make_monitor([CDS_DISC_OK], disc)
        started, release = threading.Event(), threading.Event()
        def hanging(device):
            started.set()
            release.wait(5)
            return CDTEXT
        monitor.read_cdtext = hanging
        controller = FakeController()
        self.assertTrue(monitor.poll(controller))        # READY at once
        self.assertTrue(started.wait(2))
        self.assertTrue(monitor.cdtext_pending)
        release.set()
        monitor.cancel_cdtext()
        self.assertFalse(monitor.cdtext_pending)
        self.assertEqual(monitor.loaded_cdtext[0], 'failed')

    def test_disc_on_which_cd_info_timed_out_is_not_read_again(self):
        disc = SimpleNamespace(tracks=[SimpleNamespace(number=1)])
        monitor, _calls = make_monitor([CDS_DISC_OK], disc)
        calls = []
        monitor.read_cdtext = lambda device: calls.append(1) or ('failed', {}, {}, 'cd-info timed out')
        monitor.start_cdtext(disc)
        monitor._cdtext_thread.join(2)
        monitor.start_cdtext(disc)
        self.assertEqual(len(calls), 1)
        self.assertIn('skipped', monitor.loaded_cdtext[3])


if __name__ == '__main__':
    unittest.main()


class ConsensusTests(unittest.TestCase):
    """Several editions, no CD-TEXT: their shared names are used."""

    def releases(self, *variants):
        from sonoscdplayer.metadata.musicbrainz import ReleaseCandidate
        return tuple(ReleaseCandidate(f'00000000-0000-0000-0000-00000000000{i}', album, 'The Band',
                                      None, None, titles)
                     for i, (album, titles) in enumerate(variants))

    def decide(self, releases, track_count=2):
        from sonoscdplayer.metadata.local_metadata import build_local_metadata
        from sonoscdplayer.metadata.release_matching import decide_release
        toc = SimpleNamespace(tracks=tuple(SimpleNamespace(number=n, duration_seconds=100.0)
                                           for n in range(1, track_count + 1)))
        local = build_local_metadata(toc, cdtext_status='absent')
        return local, decide_release(local, LookupResult('ambiguous', releases))

    def test_spelling_variants_still_agree(self):
        local, decision = self.decide(self.releases(
            ('…Come Down', ('Be‐In', 'Not If You Were the Last')),
            ('...Come Down', ('Be-in', 'Not If You Were The Last'))))
        self.assertEqual(decision.status, 'consensus')
        from sonoscdplayer.metadata.release_matching import merged_metadata
        merged = merged_metadata(local, decision)
        self.assertEqual(merged.album, decision.candidates[0].release.title)
        self.assertTrue(all(t.title for t in merged.tracks))

    def test_different_titles_stay_manual(self):
        _, decision = self.decide(self.releases(
            ('Album', ('One', 'Two')), ('Album', ('One', 'Bonus Mix'))))
        self.assertEqual(decision.status, 'manual')

    def test_wrong_track_count_stays_manual(self):
        _, decision = self.decide(self.releases(
            ('Album', ('One', 'Two', 'Three')), ('Album', ('One', 'Two', 'Three'))), track_count=2)
        self.assertEqual(decision.status, 'manual')


class MajorityTests(ConsensusTests):
    """Editions that differ: names more than half of them use are taken."""

    def test_majority_per_field(self):
        local, decision = self.decide(self.releases(
            ('The Moon Side', ('Intro / Breathe', 'Time')),
            ('The Moon Side', ('Intro / Breathe in the Air', 'Time')),
            ('The Moon Side', ('Intro / Breathe', 'Time')),
            ('月の側', ('Intro / Breathe', 'Time')),
            ('Moon Side', ('Intro / Breathe in the Air', 'Time'))))
        self.assertEqual(decision.status, 'majority')
        from sonoscdplayer.metadata.release_matching import merged_metadata
        merged = merged_metadata(local, decision)
        self.assertEqual((merged.album, merged.artist), ('The Moon Side', 'The Band'))
        self.assertEqual([t.title for t in merged.tracks], ['Intro / Breathe', 'Time'])

    def test_field_without_majority_stays_empty(self):
        local, decision = self.decide(self.releases(
            ('A', ('One', 'Two')), ('B', ('One', 'Two')), ('C', ('One', 'Two')), ('A', ('One', 'Two'))))
        # Album: 2 of 4 is no majority; titles: all agree.
        self.assertEqual(decision.status, 'majority')
        from sonoscdplayer.metadata.release_matching import merged_metadata
        merged = merged_metadata(local, decision)
        self.assertIsNone(merged.album)
        self.assertEqual([t.title for t in merged.tracks], ['One', 'Two'])

    def test_two_editions_that_differ_stay_manual(self):
        _, decision = self.decide(self.releases(('Album', ('One', 'Two')), ('Other', ('Un', 'Deux'))))
        self.assertEqual(decision.status, 'manual')
