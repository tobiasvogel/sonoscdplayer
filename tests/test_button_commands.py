"""Pure tests; no GPIO, player socket or Sonos access."""
import unittest

from integrations.interfaces.button_commands import map_event


class ButtonMappingTests(unittest.TestCase):
    def test_supported_press(self):
        self.assertEqual(map_event('previous', 'pressed'), 'previous')
        self.assertEqual(map_event('skip', 'pressed'), 'next')
        self.assertEqual(map_event('stop', 'pressed'), 'stop')
        self.assertEqual(map_event('playpause', 'pressed'), 'playpause')

    def test_release_and_unknown_controls_are_inert(self):
        for name in ('previous', 'skip', 'stop', 'playpause', 'encoder_sw',
                     'encoder', 'power', 'connect', 'eject', 'volume_up', 'volume_dn'):
            for event in ('released', 'right', 'left'):
                self.assertIsNone(map_event(name, event))
        for name in ('encoder_sw', 'power', 'connect', 'eject', 'volume_up', 'volume_dn'):
            self.assertIsNone(map_event(name, 'pressed'))


if __name__ == '__main__':
    unittest.main()
