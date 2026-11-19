"""Temporary MusicBrainz failures are not missing catalogue entries."""
import unittest
import urllib.error

from sonoscdplayer.metadata.musicbrainz import DiscIdentity, lookup_disc


class MusicBrainzAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.disc = DiscIdentity('A' * 28, '1 2 375 150 225')

    def test_temporary_service_errors(self):
        for code in (429, 502, 503, 504):
            with self.subTest(code=code):
                def opener(request, timeout):
                    raise urllib.error.HTTPError(
                        request.full_url, code, 'temporary', {}, None)
                result = lookup_disc(self.disc, track_count=2, opener=opener)
                self.assertEqual(result.status, 'unavailable')
                self.assertFalse(result.releases)
                self.assertIn(str(code), result.error)

    def test_404_still_not_found(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, 'missing', {}, None)
        self.assertEqual(lookup_disc(self.disc, track_count=2, opener=opener).status,
                         'not_found')

    def test_500_still_error(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 500, 'internal', {}, None)
        self.assertEqual(lookup_disc(self.disc, track_count=2, opener=opener).status,
                         'error')


if __name__ == '__main__':
    unittest.main()
