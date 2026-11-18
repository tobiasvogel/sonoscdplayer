"""META-01B: mock-only tests, no real drive or HTTP requests."""
import io
import json
import unittest
from unittest.mock import Mock
import urllib.error

from cd_toc import DiscTOC, Track
from disc_musicbrainz import (DiscIdentity, DiscIdentificationError,
                               artist_credit, identity_from_toc, lookup_disc,
                               parse_release_candidates)


class FakeDisc:
    id = 'A' * 28
    toc = '1 2 375 150 225'


class FakeResponse:
    def __init__(self, document):
        self.buffer = io.BytesIO(json.dumps(document).encode())

    def __enter__(self):
        return self.buffer

    def __exit__(self, *_):
        self.buffer.close()


class MusicBrainzTests(unittest.TestCase):
    def setUp(self):
        self.toc = DiscTOC((Track(1, 0, 75, 2, False),
                            Track(2, 75, 225, 2, False)), 225)
        self.identity = DiscIdentity('A' * 28, '1 2 375 150 225')

    def test_libdiscid_offsets_no_drive_read(self):
        fake = Mock()
        fake.put.return_value = FakeDisc()
        self.assertEqual(identity_from_toc(self.toc, libdiscid_module=fake).disc_id, 'A' * 28)
        fake.put.assert_called_once_with(1, 2, 375, [150, 225])

    def test_invalid_toc_rejected(self):
        fake = Mock()
        with self.assertRaises(DiscIdentificationError):
            identity_from_toc(DiscTOC((Track(2, 0, 75, 2, False),), 75),
                              libdiscid_module=fake)
        fake.put.assert_not_called()

    def test_no_results(self):
        result = lookup_disc(self.identity, track_count=2,
                             opener=lambda req, timeout: FakeResponse({'releases': []}))
        self.assertEqual(result.status, 'not_found')

    def test_unique(self):
        document = {'releases': [{'id': 'one', 'title': 'Album',
                     'artist-credit': [{'name': 'Band'}],
                     'media': [{'track-count': 2, 'tracks': [
                         {'title': 'First'}, {'recording': {'title': 'Second'}}]}]}]}
        calls = []
        def opener(request, timeout):
            calls.append(request)
            return FakeResponse(document)
        result = lookup_disc(self.identity, track_count=2, opener=opener)
        self.assertEqual(result.status, 'unique')
        self.assertEqual(result.selected_release_id, 'one')
        self.assertEqual(result.releases[0].track_titles, ('First', 'Second'))
        self.assertIn('toc=', calls[0].full_url)
        self.assertIn('SonosCDPlayer', calls[0].get_header('User-agent'))

    def test_compilation_keeps_artist_per_track(self):
        document = {'releases': [{'id': 'one', 'title': 'Hits', 'artist-credit': [
                        {'name': 'Various Artists'}],
                     'media': [{'track-count': 3, 'tracks': [
                         {'title': 'A', 'artist-credit': [{'name': 'Singer', 'joinphrase': ' feat. '},
                                                          {'name': 'Guest'}]},
                         {'title': 'B', 'recording': {'artist-credit': [{'name': 'Band'}]}},
                         {'title': 'C'}]}]}]}
        result = lookup_disc(self.identity, track_count=3,
                             opener=lambda req, timeout: FakeResponse(document))
        release = result.releases[0]
        self.assertEqual(release.artist, 'Various Artists')
        self.assertEqual(release.track_artists, ('Singer feat. Guest', 'Band', None))

    def test_ambiguous(self):
        document = {'releases': [{'id': 'one', 'title': 'A'},
                                  {'id': 'two', 'title': 'B'}]}
        result = lookup_disc(self.identity, track_count=2,
                             opener=lambda req, timeout: FakeResponse(document))
        self.assertEqual(result.status, 'ambiguous')
        self.assertIsNone(result.selected_release_id)

    def test_http_404(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, 'missing', {}, None)
        self.assertEqual(lookup_disc(self.identity, track_count=2, opener=opener).status,
                         'not_found')

    def test_offline_error_sanitized(self):
        def opener(request, timeout):
            raise urllib.error.URLError('private host details')
        result = lookup_disc(self.identity, track_count=2, opener=opener)
        self.assertEqual(result.status, 'offline')
        self.assertNotIn('private host details', result.error)

    def test_malformed_response(self):
        result = lookup_disc(self.identity, track_count=2,
                             opener=lambda req, timeout: FakeResponse([]))
        self.assertEqual(result.status, 'error')

    def test_artist_credit(self):
        self.assertEqual(artist_credit([{'name': 'A', 'joinphrase': ' & '},
                                        {'name': 'B'}]), 'A & B')

    def test_duplicate_release_and_ambiguous_medium(self):
        data = {'releases': [{'id': 'one', 'title': 'Album', 'media': [
            {'track-count': 2, 'tracks': [{'title': 'A'}, {'title': 'B'}]},
            {'track-count': 2, 'tracks': [{'title': 'C'}, {'title': 'D'}]}]},
            {'id': 'one', 'title': 'Duplicate'}]}
        parsed = parse_release_candidates(data, track_count=2)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].track_titles, ())


if __name__ == '__main__':
    unittest.main()
