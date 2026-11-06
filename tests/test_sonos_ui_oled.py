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

    def test_real_no_disc_snapshot_is_renderable(self):
        # PlayerState.NO_DISC reports selected_track=None, track_count=0.
        payload=state('NO_DISC', None, 0)
        frame=oled_status.modern_status_frame(payload)
        self.assertIsInstance(frame, Framebuffer)
        self.assertEqual(len(frame.data), 8192)

    def test_unexpected_mode_falls_back_without_repeating_exception(self):
        payload=state('FUTURE_PLAYER_STATE', None, 0)
        with patch.object(oled_status, 'status_frame', return_value=Framebuffer()) as fallback:
            frame=oled_status.safe_modern_status_frame(payload,language='de')
        self.assertIsInstance(frame, Framebuffer)
        fallback.assert_called_once_with(error='invalid player status',language='de')

    def test_invalid_cached_track_information_has_safe_fallback(self):
        payload=state('STOPPED', None, 12)
        frame=oled_status.safe_modern_status_frame(payload, language='en')
        self.assertIsInstance(frame, Framebuffer)

    def test_modern_offline_reuses_existing_error_screen(self):
        with patch.object(oled_status, 'status_frame', return_value=Framebuffer()) as existing:
            oled_status.modern_status_frame(error='socket refused')
        existing.assert_called_once_with(error='socket refused')

    def test_bad_schema_is_rejected_without_invented_metadata(self):
        with self.assertRaises(ValueError):
            oled_status.modern_status_frame({'schema_version': 99, 'player': {}})


if __name__ == '__main__':
    unittest.main()
