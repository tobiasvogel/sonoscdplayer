"""Regression checks for opt-in Modern Hi-Fi rendering on existing OLED path."""
import unittest
from unittest.mock import patch

from integrations.outputs import oled_status
from integrations.outputs.ssd1327 import Framebuffer


def state(mode='PLAYING', track=2, count=11):
    return {'schema_version': 1,
            'player': {'mode': mode, 'track': track, 'track_count': count}}


class ModernOLEDTests(unittest.TestCase):
    def test_modern_frame_is_valid_for_playback(self):
        frame = oled_status.modern_status_frame(state())
        self.assertIsInstance(frame, Framebuffer)
        self.assertEqual(len(frame.data), 128 * 128 // 2)

    def test_modern_frame_supports_idle(self):
        frame = oled_status.modern_status_frame(state('STOPPED', 0, 0))
        self.assertIsInstance(frame, Framebuffer)

    def test_modern_offline_reuses_existing_error_screen(self):
        with patch.object(oled_status, 'status_frame', return_value=Framebuffer()) as existing:
            oled_status.modern_status_frame(error='socket refused')
        existing.assert_called_once_with(error='socket refused', language='de')

    def test_bad_schema_is_rejected_without_invented_metadata(self):
        with self.assertRaises(ValueError):
            oled_status.modern_status_frame({'schema_version': 99, 'player': {}})


if __name__ == '__main__':
    unittest.main()
