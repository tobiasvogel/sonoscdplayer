"""Excluded hardware remains accessible only as an explicit reference profile."""
import unittest
from sonos_ui.display_profiles import PROFILES, REFERENCE_PROFILES, profile_for


class DisplayCandidateTests(unittest.TestCase):
    def test_four_color_epaper_is_not_a_player_display_candidate(self):
        self.assertNotIn('epaper_200x200_4color', PROFILES)
        with self.assertRaises(KeyError):
            profile_for('epaper_200x200_4color')

    def test_reference_geometry_is_preserved_without_claiming_runtime_support(self):
        profile = REFERENCE_PROFILES['epaper_200x200_4color']
        self.assertEqual((profile.width, profile.height), (200, 200))
        self.assertEqual(profile.color_mode, 'four_color')
        self.assertFalse(profile.driver_ready)

    def test_oled_and_lcd_candidates_remain_available(self):
        for key in ('ssd1327_128x128', 'sh1107_64x128', 'st7735s_128x160'):
            self.assertEqual(profile_for(key).key, key)


if __name__ == '__main__':
    unittest.main()
