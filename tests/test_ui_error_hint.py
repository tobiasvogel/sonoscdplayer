"""After a player error the overview tells how to go on (18.11.)."""
import unittest
from unittest.mock import patch

from sonos_ui.player_adapter import project_status
from sonos_ui.render import Renderer


def footers(mode, error=None):
    state = {'schema_version': 1,
             'player': {'mode': mode, 'track': 3, 'track_count': 12, 'error': error},
             'sonos': {'owned': False}}
    renderer = Renderer(language='de')
    seen = []
    original = renderer.bottom

    def bottom(canvas, *, footer, **kwargs):
        seen.append(footer)
        return original(canvas, footer=footer, **kwargs)
    with patch.object(renderer, 'bottom', bottom):
        renderer.render(project_status(state))
    return seen


class ErrorHintTests(unittest.TestCase):
    def test_error_shows_play_and_stop_hint(self):
        self.assertEqual(footers('ERROR', 'PLAY failed'), ['PLAY: erneut · STOP: OK'])
        self.assertEqual(footers('STOPPED'), ['Drücken: Menü'])


if __name__ == '__main__':
    unittest.main()
