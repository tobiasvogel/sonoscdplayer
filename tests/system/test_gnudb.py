"""M4c.1: offline CDDB ID, HTTP query and strict xmcd parsing tests."""
from contextlib import contextmanager
import io
import urllib.error
import unittest

from sonoscdplayer.audio.table_of_contents import DiscTOC, Track
from sonoscdplayer.metadata.gnudb import (
    CDDBError, CDDBMatch, identity_from_toc, parse_query, parse_entry,
    query_disc, read_entry,
)


def toc():
    return DiscTOC((Track(1, 0, 7500, 2, False),
                    Track(2, 7500, 15000, 2, False)), 15000)


class FakeResponse:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, size):
        return self.data[:size]


def xmcd(offsets='# 150\n# 7650', length='200', track2='Second'):
    return (f'210 data 0100c802 CD database entry follows\n'
            f'# xmcd\n# Track frame offsets:\n{offsets}\n#\n'
            f'# Disc length: {length} seconds\n#\n'
            f'DISCID=0100c802\nDTITLE=Sample Artist / Sample Album\n'
            f'DYEAR=1999\nTTITLE0=First\nTTITLE1={track2}\n.\n').encode()


class GnuDBTests(unittest.TestCase):
    def test_id_uses_150_frame_leadin_and_truncated_seconds(self):
        identity = identity_from_toc(toc())
        # decimal digit sums for seconds 2 and 102 = 2+3 = 5
        self.assertEqual(identity.disc_id, '0500c802')
        self.assertEqual(identity.offsets, (150, 7650))
        self.assertEqual(identity.duration_seconds, 200)
        self.assertEqual(identity.query_command,
                         'cddb query 0500c802 2 150 7650 200')

    def test_reject_invalid_or_incomplete_toc(self):
        for value in (DiscTOC((), 0),
                      DiscTOC((Track(2, 0, 7500, 2, False),), 7500),
                      DiscTOC((Track(1, 0, 7500, 2, False),), 8000),
                      DiscTOC((Track(1, -75, 7500, 2, False),), 7500)):
            with self.subTest(value=value), self.assertRaises(CDDBError):
                identity_from_toc(value)

    def test_query_unique_and_request_encoding(self):
        sent = []
        def opener(req, timeout):
            sent.append((req.full_url, timeout))
            return FakeResponse(b'200 data 0100c802 Artist / Album\n')
        result = query_disc(identity_from_toc(toc()), opener=opener)
        self.assertEqual(result.status, 'unique')
        self.assertEqual(result.matches[0].description, 'Artist / Album')
        self.assertIn('cmd=cddb+query+0500c802+2+150+7650+200', sent[0][0])
        self.assertIn('proto=6', sent[0][0])
        self.assertEqual(sent[0][1], 5)

    def test_query_ambiguous_inexact_and_not_found(self):
        for response, status in (
            (b'202 No match\n', 'not_found'),
            (b'211 inexact matches\ndata 0100c802 A / B\n.\n', 'ambiguous'),
            (b'210 exact matches\ndata 0100c802 A / B\nrock 0200c802 C / D\n.\n', 'ambiguous')):
            with self.subTest(status=status):
                self.assertEqual(parse_query(response).status, status)

    def test_reject_truncated_or_invalid_query(self):
        for raw in (b'', b'210 Found\ndata 0100c802 A / B\n',
                    b'200 too-short\n', b'210 Found\n.\n',
                    b'200 data INVALID A / B\n'):
            with self.subTest(raw=raw), self.assertRaises(CDDBError):
                parse_query(raw)

    def test_http_overload_and_offline_are_nonfatal(self):
        def overloaded(_req, timeout):
            raise urllib.error.HTTPError('https://example', 503, 'busy', {}, None)
        def offline(_req, timeout):
            raise urllib.error.URLError('no network')
        identity = identity_from_toc(toc())
        self.assertEqual(query_disc(identity, opener=overloaded).status, 'unavailable')
        self.assertEqual(query_disc(identity, opener=offline).status, 'offline')

    def test_read_entry_parses_verified_toc_without_musicbrainz_id(self):
        identity = identity_from_toc(toc())
        match = CDDBMatch('data', '0100c802', 'Artist / Album')
        entry = read_entry(identity, match, opener=lambda req, timeout: FakeResponse(xmcd()))
        self.assertEqual(entry.album, 'Sample Album')
        self.assertEqual(entry.artist, 'Sample Artist')
        self.assertEqual(entry.track_titles, ('First', 'Second'))
        self.assertEqual(entry.year, '1999')
        self.assertFalse(hasattr(entry, 'release_id'))

    def test_read_rejects_collisions_and_missing_tracks(self):
        identity = identity_from_toc(toc())
        match = CDDBMatch('data', '0100c802', 'Artist / Album')
        for raw in (xmcd(offsets='# 150\n# 7750'),
                    xmcd(length='199'), xmcd(track2=''),
                    xmcd().replace(b'TTITLE1=Second\n', b''),
                    xmcd().replace(b'DISCID=0100c802', b'DISCID=ffffffff')):
            with self.subTest(raw=raw), self.assertRaises(CDDBError):
                parse_entry(raw, identity=identity, match=match)

    def test_invalid_https_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            query_disc(identity_from_toc(toc()), endpoint='http://example.invalid/cgi',
                       opener=lambda req, timeout: FakeResponse(b'202 No match\n'))


if __name__ == '__main__':
    unittest.main()
