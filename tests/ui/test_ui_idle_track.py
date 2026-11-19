"""Idle overview shows the selected track, not always 01."""
import unittest

from sonoscdplayer.ui.menu.player_adapter import project_status
from sonoscdplayer.ui.renderers.oled_128 import Renderer


def idle(track, metadata=None):
    state = {'schema_version': 1,
             'player': {'mode': 'STOPPED', 'track': track, 'track_count': 12, 'error': None},
             'sonos': {'owned': False}}
    if metadata:
        state['metadata'] = metadata
    return Renderer(language='de').render(project_status(state)).crop((0, 60, 128, 82))


class IdleTrackTests(unittest.TestCase):
    def test_number_follows_selection(self):
        self.assertNotEqual(idle(1).tobytes(), idle(7).tobytes())
        self.assertEqual(idle(7).tobytes(), idle(7).tobytes())


if __name__ == '__main__':
    unittest.main()
