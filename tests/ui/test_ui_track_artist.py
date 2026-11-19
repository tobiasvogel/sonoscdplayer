"""Compilations: during playback the OLED shows the track's own artist."""
import unittest

from sonoscdplayer.ui.menu.player_adapter import project_status
from sonoscdplayer.ui.renderers.oled_128 import Renderer


def state(album_artist, track_artist, mode='PLAYING'):
    return {'schema_version': 1,
            'player': {'mode': mode, 'track': 2, 'track_count': 12, 'error': None},
            'sonos': {'owned': True},
            'metadata': {'album': 'Hits', 'artist': album_artist,
                         'track_title': 'Song', 'track_artist': track_artist}}


class TrackArtistTests(unittest.TestCase):
    def test_only_a_differing_track_artist_is_shown(self):
        self.assertEqual(project_status(state('Various Artists', 'Singer')).track_artist, 'Singer')
        self.assertIsNone(project_status(state('Band', 'Band')).track_artist)
        self.assertIsNone(project_status(state('Band', None)).track_artist)

    def test_playback_line_changes_only_for_compilations(self):
        def lines(s):
            return Renderer(language='de').render(project_status(s)).crop((0, 40, 128, 60)).tobytes()
        self.assertNotEqual(lines(state('Various Artists', 'Singer')), lines(state('Band', 'Band')))
        self.assertEqual(lines(state('Band', 'Band')), lines(state('Band', None)))


if __name__ == '__main__':
    unittest.main()
