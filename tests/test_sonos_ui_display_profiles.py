"""Profile-selection tests; no new hardware is touched."""
import unittest
from sonos_ui.display_profiles import PROFILES, profile_for, HardwareDisplayProfile


class DisplayProfileTests(unittest.TestCase):
    def test_four_explicit_profiles(self):
        self.assertEqual(len(PROFILES),4)
        self.assertEqual({p.controller for p in PROFILES.values()},
                         {'SSD1327','ST7735S','SH1107','UNKNOWN'})

    def test_existing_oled_stays_text_focused(self):
        p=profile_for('ssd1327_128x128')
        self.assertFalse(p.cover_enabled())
        self.assertTrue(p.driver_ready)

    def test_tft_proposes_cover(self):
        p=profile_for('st7735s_128x160')
        self.assertTrue(p.cover_enabled())
        self.assertFalse(p.driver_ready)

    def test_narrow_monochrome_omits_cover(self):
        p=profile_for('sh1107_64x128')
        self.assertFalse(p.cover_enabled())

    def test_eink_refresh_only_on_events(self):
        p=profile_for('epaper_200x200_4color')
        self.assertEqual(p.refresh_policy,'event_driven')
        self.assertEqual(p.controller,'UNKNOWN')
        self.assertFalse(p.driver_ready)

    def test_user_override_always_applies(self):
        for p in PROFILES.values():
            self.assertTrue(p.cover_enabled('on'))
            self.assertFalse(p.cover_enabled('off'))

    def test_invalid_profile_fails(self):
        with self.assertRaises(ValueError):
            HardwareDisplayProfile('bad','X',0,128,'mono1',False,'interactive')


if __name__=='__main__':
    unittest.main()
