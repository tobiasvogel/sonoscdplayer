"""M4c.3: provider-aware cache and metadata provenance (offline only)."""
from pathlib import Path
import tempfile
import unittest

from sonoscdplayer.audio.table_of_contents import DiscTOC, Track
from sonoscdplayer.metadata.cache import DiscCache
from sonoscdplayer.metadata.gnudb import CDDBEntry, CDDBMatch, CDDBQueryResult
from sonoscdplayer.metadata.gnudb import identity_from_toc as cddb_identity
from sonoscdplayer.metadata.local_metadata import local_metadata_from_cdtext
from sonoscdplayer.metadata.musicbrainz import DiscIdentity, LookupResult, ReleaseCandidate
from sonoscdplayer.metadata.worker import _safe_result, describe
from sonoscdplayer.metadata.release_matching import MatchingPolicy


class NoCover:
    def get_or_download(self, *args, **kwargs):
        raise AssertionError('GnuDB identity must never reach the cover API')


class CoverEnabled:
    enabled = True


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = DiscCache(Path(self.tmp.name) / 'cache.sqlite3')
        self.toc = DiscTOC((Track(1, 0, 75 * 180, 2, False),
                            Track(2, 75 * 180, 75 * 400, 2, False)), 75 * 400)
        self.ident = DiscIdentity('a' * 28, '1 2 30250 150 13650')
        self.cddb = cddb_identity(self.toc)
        self.match = CDDBMatch('rock', self.cddb.disc_id, 'Artist / Album')
        self.entry = CDDBEntry('rock', self.cddb.disc_id, 'Album', 'Artist',
                               ('First', 'Second'), (None, None))
        self.queries = 0
        self.mb_calls = 0

    def query(self, identity, *, timeout_seconds):
        self.queries += 1
        return CDDBQueryResult('unique', (self.match,))

    def read(self, identity, match, *, timeout_seconds):
        return self.entry

    def run_worker(self, mb_result=None, cdtext=None, enabled=True):
        def mb(*args, **kwargs):
            self.mb_calls += 1
            return mb_result if mb_result is not None else LookupResult('unavailable')
        return _safe_result(self.toc, cdtext, cache=self.cache, cover_cache=NoCover(),
                            cover_policy=CoverEnabled(), online=True, timeout=8,
                            policy=MatchingPolicy(), identify=lambda toc: self.ident,
                            lookup=mb, gnudb_enabled=enabled, gnudb_timeout=5,
                            gnudb_query=self.query, gnudb_read=self.read)

    def test_gnudb_roundtrip_without_musicbrainz_release(self):
        local = local_metadata_from_cdtext(self.toc)
        self.assertTrue(self.cache.put(self.ident, local, match_method='gnudb'))
        got = self.cache.get(self.ident)
        self.assertEqual(got.match_method, 'gnudb')
        self.assertIsNone(got.release_id)
        self.assertIsNone(got.confidence_score)

    def test_fallback_persisted_and_reused_without_second_gnudb_query(self):
        first = self.run_worker()
        self.assertEqual(first['metadata_source'], 'gnudb')
        self.assertIsNone(first['release_id'])
        self.assertEqual(first['cover_status'], 'no_release')
        self.assertEqual(self.cache.get(self.ident).match_method, 'gnudb')
        self.assertEqual(self.queries, 1)
        again = self.run_worker()
        self.assertEqual(again['album'], 'Album')
        self.assertEqual(again['metadata_source'], 'gnudb')
        self.assertEqual(self.queries, 1)
        self.assertEqual(self.mb_calls, 2)
        self.assertIn('source=gnudb', describe(again))

    def test_musicbrainz_can_replace_cached_gnudb(self):
        self.run_worker()
        mb = LookupResult('unique', (ReleaseCandidate(
            'musicbrainz-release', 'MB album', 'MB artist', None, None,
            ('MB first', 'MB second')),))
        value = self.run_worker(mb_result=mb)
        self.assertEqual(value['release_id'], 'musicbrainz-release')
        self.assertEqual(value['metadata_source'], 'musicbrainz')
        entry = self.cache.get(self.ident)
        self.assertEqual(entry.release_id, 'musicbrainz-release')
        self.assertEqual(entry.match_method, 'unique')

    def test_gnudb_cannot_replace_existing_musicbrainz_binding(self):
        local = local_metadata_from_cdtext(self.toc)
        self.cache.put(self.ident, local, release_id='existing', match_method='unique')
        self.assertFalse(self.cache.put(self.ident, local, match_method='gnudb'))
        self.assertEqual(self.cache.get(self.ident).release_id, 'existing')

    def test_manual_confirmation_still_protected(self):
        local = local_metadata_from_cdtext(self.toc)
        self.cache.put(self.ident, local, release_id='manual', match_method='manual',
                       confirmed_by_user=True)
        self.assertFalse(self.cache.put(self.ident, local, match_method='gnudb'))
        self.assertTrue(self.cache.get(self.ident).confirmed_by_user)

    def test_gnudb_cannot_claim_musicbrainz_release_id(self):
        with self.assertRaisesRegex(ValueError, 'no MusicBrainz release ID'):
            self.cache.put(self.ident, local_metadata_from_cdtext(self.toc),
                           release_id='bogus', match_method='gnudb')

    def test_cached_gnudb_conflicting_cdtext_is_ignored(self):
        self.run_worker()
        cdtext = ('available', {'TITLE': 'Different Album'}, {}, None)
        result = self.run_worker(cdtext=cdtext)
        self.assertEqual(result['album'], 'Different Album')
        self.assertEqual(result['metadata_source'], 'cdtext')
        self.assertNotEqual(result['match_status'], 'gnudb')

    def test_disabled_fallback_does_not_use_saved_gnudb(self):
        self.run_worker()
        result = self.run_worker(enabled=False)
        self.assertIsNone(result['album'])
        self.assertEqual(result['metadata_source'], 'toc')
        self.assertEqual(self.queries, 1)

    def test_local_cdtext_source_without_fallback(self):
        result = self.run_worker(cdtext=('available', {'TITLE': 'Local'}, {}, None),
                                 enabled=False)
        self.assertEqual(result['metadata_source'], 'cdtext')
        self.assertIsNone(result['release_id'])


if __name__ == '__main__':
    unittest.main()
