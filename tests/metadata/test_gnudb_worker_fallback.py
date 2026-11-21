"""M4c.2: fallback integration must preserve existing provider/cache semantics."""
from dataclasses import replace
import unittest

from sonoscdplayer.audio.table_of_contents import DiscTOC, Track
from sonoscdplayer.metadata.gnudb import CDDBEntry, CDDBMatch, CDDBQueryResult, identity_from_toc as cddb_id
from sonoscdplayer.metadata.musicbrainz import DiscIdentity, LookupResult
from sonoscdplayer.metadata.worker import _safe_result


class _Cache:
    def __init__(self, hit=None):
        self.hit = hit
        self.writes = []

    def get(self, identity):
        return self.hit

    def put(self, *args, **kwargs):
        self.writes.append((args, kwargs))


class _Cover:
    def get_or_download(self, *args, **kwargs):
        raise AssertionError('CDDB IDs must not be queried for MusicBrainz cover art')


class _CoverPolicy:
    enabled = True


class GnuDBWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.toc = DiscTOC((Track(1, 0, 75*180, 2, False),
                            Track(2, 75*180, 75*400, 2, False)), 75*400)
        self.cache = _Cache()
        cid = cddb_id(self.toc)
        self.hit = CDDBMatch('rock', cid.disc_id, 'Artist / Album')
        self.entry = CDDBEntry('rock', cid.disc_id, 'Album', 'Artist',
                               ('First', 'Second'), (None, None))
        self.gnudb_calls = []

    def query(self, identity, *, timeout_seconds):
        self.gnudb_calls.append(('query', timeout_seconds))
        return CDDBQueryResult('unique', (self.hit,))

    def read(self, identity, match, *, timeout_seconds):
        self.gnudb_calls.append(('read', timeout_seconds))
        return self.entry

    def run_lookup(self, musicbrainz, **kw):
        return _safe_result(self.toc, cache=self.cache, cover_cache=_Cover(),
                            cover_policy=_CoverPolicy(), online=True, timeout=8,
                            policy=None, identify=lambda toc: DiscIdentity('a'*28, 'toc'),
                            lookup=lambda *a, **k: musicbrainz,
                            gnudb_enabled=True, gnudb_timeout=5,
                            gnudb_query=self.query, gnudb_read=self.read, **kw)

    def test_musicbrainz_overload_uses_gnudb_without_cover_or_cache(self):
        result = self.run_lookup(LookupResult('unavailable', error='MusicBrainz temporarily unavailable'))
        self.assertEqual(result['album'], 'Album')
        self.assertEqual(result['match_status'], 'gnudb')
        self.assertEqual(result['lookup_status'], 'unavailable')
        self.assertIsNone(result['release_id'])
        self.assertEqual(result['cover_status'], 'no_release')
        self.assertFalse(self.cache.writes)
        self.assertEqual(len(self.gnudb_calls), 2)

    def test_musicbrainz_not_found_uses_fallback(self):
        result = self.run_lookup(LookupResult('not_found'))
        self.assertEqual(result['match_status'], 'gnudb')

    def test_musicbrainz_ambiguous_does_not_fallback(self):
        result = self.run_lookup(LookupResult('ambiguous'))
        self.assertEqual(self.gnudb_calls, [])
        self.assertEqual(result['lookup_status'], 'ambiguous')

    def test_fallback_ambiguous_cannot_replace_cdtext(self):
        self.query = lambda *a, **k: CDDBQueryResult('ambiguous', (self.hit, self.hit))
        result = self.run_lookup(LookupResult('unavailable'))
        self.assertEqual(result['match_status'], 'toc')
        self.assertIsNone(result['release_id'])

    def test_disabled_fallback_produces_no_calls(self):
        # Direct call with explicit flag, no live network ever allowed.
        result = _safe_result(self.toc, cache=self.cache, cover_cache=_Cover(),
                    cover_policy=_CoverPolicy(), online=True, timeout=8,
                    policy=None, identify=lambda toc: DiscIdentity('a'*28, 'toc'),
                    lookup=lambda *a, **k: LookupResult('unavailable'),
                    gnudb_enabled=False, gnudb_query=self.query, gnudb_read=self.read)
        self.assertEqual(result['match_status'], 'toc')
        self.assertEqual(self.gnudb_calls, [])


if __name__ == '__main__':
    unittest.main()
