"""Disc metadata in the status and on the OLED."""
import json
import unittest

from metadata_status import MAX_TEXT, status_metadata
from sonos_ui.player_adapter import project_status
from sonos_ui.render import Renderer

FULL = {
    'disc_id': 'A' * 28, 'album': 'Album', 'artist': 'Album Artist',
    'tracks': [{'number': n, 'title': f'Title {n} ' + 'x' * 200, 'artist': None,
                'duration_seconds': 200.0} for n in range(1, 100)],
    'release_id': None, 'match_status': 'unique', 'match_score': 95,
    'cover_file': None, 'cdtext_status': 'available', 'lookup_status': 'unique',
}


def status(mode='STOPPED', track=1, metadata=None, count=99):
    state = {'schema_version': 1,
             'player': {'mode': mode, 'track': track, 'track_count': count, 'error': None},
             'sonos': {'owned': False}}
    if metadata is not None:
        state['metadata'] = metadata
    return state


class StatusMetadataTests(unittest.TestCase):
    def test_compact_and_bounded(self):
        meta = status_metadata(FULL, 7)
        self.assertNotIn('tracks', meta)
        self.assertTrue(meta['track_title'].startswith('Title 7'))
        self.assertLessEqual(len(meta['track_title']), MAX_TEXT)
        # Whole status reply stays far below the LED reader's 8 KB limit.
        reply = json.dumps({'ok': True, 'state': status(metadata=meta)})
        self.assertLess(len(reply.encode()), 2048)

    def test_missing_track_and_garbage(self):
        meta = status_metadata(FULL, 150)
        self.assertIsNone(meta['track_title'])
        self.assertIsNone(status_metadata(None, 1))
        self.assertIsNone(status_metadata({'album': 'a\x00b'}, 1)['track_title'])
        self.assertEqual(status_metadata({'album': 'a\x00b'}, 1)['album'], 'ab')


class AdapterTests(unittest.TestCase):
    def test_metadata_reaches_display_model(self):
        model = project_status(status(metadata=status_metadata(FULL, 1)))
        self.assertEqual((model.album, model.artist), ('Album', 'Album Artist'))
        self.assertTrue(model.title.startswith('Title 1'))

    def test_track_artist_when_no_album_artist(self):
        meta = status_metadata({'tracks': [{'number': 1, 'title': 'T', 'artist': 'Solo'}]}, 1)
        model = project_status(status(metadata=meta))
        self.assertEqual(model.artist, 'Solo')

    def test_no_metadata_without_disc(self):
        model = project_status(status('NO_DISC', None, status_metadata(FULL, 1), count=0))
        self.assertIsNone(model.album)

    def test_cover_only_as_absolute_path(self):
        meta = dict(status_metadata(FULL, 1), cover_file='relative.jpg')
        self.assertIsNone(project_status(status(metadata=meta)).cover)

    def test_renders_idle_and_playback_with_metadata(self):
        for mode in ('STOPPED', 'PLAYING'):
            model = project_status(status(mode, metadata=status_metadata(FULL, 1)))
            image = Renderer(language='de').render(model)
            self.assertEqual(image.size, (128, 128))


if __name__ == '__main__':
    unittest.main()
