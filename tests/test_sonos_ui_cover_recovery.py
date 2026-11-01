"""Regression checks for cover policy and safe SSD1327 firmware adoption."""
import unittest
from unittest.mock import Mock
from sonos_ui.layout_config import DisplayProfile, show_cover
from sonos_ui.model import DisplayModel
from sonos_ui.render import Renderer
from integrations.outputs.ssd1327 import SSD1327, DisplayConfig


class CoverAndRecoveryTests(unittest.TestCase):
    def test_small_oled_auto_omits_cover(self):
        self.assertFalse(show_cover('auto', DisplayProfile()))

    def test_large_profile_can_enable_auto_cover(self):
        profile = DisplayProfile(320, 320, supports_cover_auto=True)
        self.assertTrue(show_cover('auto', profile))

    def test_cover_override_on_and_off(self):
        self.assertTrue(show_cover('on'))
        self.assertFalse(show_cover('off', DisplayProfile(320,320,True)))

    def test_cover_mode_rejects_typos(self):
        with self.assertRaises(ValueError):
            show_cover('sometimes')

    def test_render_both_layouts(self):
        model=DisplayModel(view='playback',playback='playing',track=2,track_count=10,
                           title='Ein längerer Titel',album='Albumname')
        for setting in ('auto','on','off'):
            image=Renderer(cover_mode=setting).render(model,grayscale=True)
            self.assertEqual(image.size,(128,128))

    def test_cover_free_layout_differs_from_cover_layout(self):
        model=DisplayModel(view='idle',disc_present=True,album='Album',
                           artist='Interpret',track_count=12)
        no_cover=Renderer(cover_mode='off').render(model)
        with_cover=Renderer(cover_mode='on').render(model)
        self.assertNotEqual(no_cover.tobytes(),with_cover.tobytes())

    def test_adopt_sends_display_on_without_hardware_reset(self):
        gpio=Mock()
        gpio.BCM=11
        gpio.OUT=1
        gpio.LOW=0
        gpio.HIGH=1
        spi=Mock()
        driver=SSD1327(DisplayConfig(),spi=spi,gpio=gpio)
        driver.open(adopt_existing=True)
        self.assertTrue(driver.opened)
        spi.writebytes.assert_called_once_with([0xaf])
        self.assertFalse(any(call.args[0] == driver.config.rst_pin
                             for call in gpio.output.call_args_list))
        # Avoid close(): it deliberately sends DISPLAY OFF and is not part of
        # testing the wake-up sequence.


if __name__=='__main__':
    unittest.main()
