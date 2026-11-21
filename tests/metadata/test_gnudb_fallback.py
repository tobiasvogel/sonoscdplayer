"""M4c.2: conservative, network-free GnuDB integration and provenance tests."""
from dataclasses import replace
import unittest

from sonoscdplayer.audio.table_of_contents import DiscTOC, Track
from sonoscdplayer.metadata.gnudb import CDDBEntry, CDDBMatch, CDDBQueryResult, identity_from_toc
from sonoscdplayer.metadata.gnudb_fallback import resolve_fallback
from sonoscdplayer.metadata.local_metadata import local_metadata_from_cdtext


def toc():
    return DiscTOC((Track(1, 0, 75*180, 2, False),
                    Track(2, 75*180, 75*400, 2, False)), 75*400)


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.toc = toc()
        identity = identity_from_toc(self.toc)
        self.hit = CDDBMatch('rock', identity.disc_id, 'Artist / Album')
        self.entry = CDDBEntry('rock', identity.disc_id, 'Album', 'Artist',
                               ('First', 'Second'), (None, None))
        self.queries = []
        self.reads = []

    def query(self, identity, *, timeout_seconds):
        self.queries.append(timeout_seconds)
        return CDDBQueryResult('unique', (self.hit,))

    def read(self, identity, hit, *, timeout_seconds):
        self.reads.append(timeout_seconds)
        return self.entry

    def run_fallback(self, local=None, **kwargs):
        return resolve_fallback(self.toc, local or local_metadata_from_cdtext(self.toc),
                                query=self.query, read=self.read, **kwargs)

    def test_exact_unique_fills_missing_titles(self):
        result, status = self.run_fallback()
        self.assertEqual(status, 'unique')
        self.assertEqual((result.album, result.artist), ('Album', 'Artist'))
        self.assertEqual([t.title for t in result.tracks], ['First', 'Second'])
        self.assertEqual(result.cdtext_status, 'disabled')
        self.assertEqual(self.queries, [5]); self.assertEqual(self.reads, [5])

    def test_existing_cdtext_is_never_overwritten(self):
        local = local_metadata_from_cdtext(self.toc, ('available', {'TITLE': 'Album', 'PERFORMER': 'Artist'},
                    {1: {'TITLE': 'First', 'ISRC': 'USABC1234567'}}, None))
        result, status = self.run_fallback(local)
        self.assertEqual(status, 'unique')
        self.assertEqual(result.tracks[0].title, 'First')
        self.assertEqual(result.tracks[0].isrc, 'USABC1234567')
        self.assertEqual(result.cdtext_status, 'available')

    def test_conflicting_cdtext_rejects_entry(self):
        local = local_metadata_from_cdtext(self.toc, ('available', {'TITLE':'Other'}, {}, None))
        result, status = self.run_fallback(local)
        self.assertIsNone(result); self.assertEqual(status, 'unverified')

    def test_ambiguous_query_not_read(self):
        self.query = lambda identity, **kwargs: CDDBQueryResult('ambiguous', (self.hit, self.hit))
        result, status = self.run_fallback()
        self.assertIsNone(result); self.assertEqual(status, 'ambiguous')
        self.assertEqual(self.reads, [])

    def test_404_not_found_does_not_read(self):
        self.query = lambda identity, **kwargs: CDDBQueryResult('not_found')
        result, status = self.run_fallback()
        self.assertIsNone(result); self.assertEqual(status, 'not_found')
        self.assertEqual(self.reads, [])

    def test_cddb_id_collision_rejected(self):
        self.hit = replace(self.hit, disc_id='deadbeef')
        result, status = self.run_fallback()
        self.assertIsNone(result); self.assertEqual(status, 'unverified')
        self.assertEqual(self.reads, [])

    def test_read_record_inconsistent_with_query_rejected(self):
        self.entry = replace(self.entry, category='misc')
        result, status = self.run_fallback()
        self.assertIsNone(result); self.assertEqual(status, 'unverified')

    def test_network_failure_is_isolated(self):
        def fail(*args, **kwargs):
            raise OSError('sensitive details')
        self.query = fail
        result, status = self.run_fallback()
        self.assertIsNone(result); self.assertEqual(status, 'error')

    def test_invalid_timeout_fails_closed(self):
        with self.assertRaises(ValueError):
            self.run_fallback(timeout_seconds=0)
        self.assertEqual(self.queries, [])


if __name__ == '__main__':
    unittest.main()
