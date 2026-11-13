"""Localized start screen and OLED stop ordering (offline)."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))

import system_screen  # noqa: E402
from sonos_ui.i18n import available_languages  # noqa: E402
from integrations.outputs.oled_status import (  # noqa: E402
    DEFAULT_LOGO, load_splash, start_splash)


def text_columns(image):
    return [x for x in range(128)
            if any(image.getpixel((x, y)) > 40 for y in range(system_screen.ICON_BOTTOM, 128))]


class SystemScreenTests(unittest.TestCase):
    def test_all_screens_fit_centered_in_every_language(self):
        for language in available_languages():
            for name in system_screen.SCREENS:
                with self.subTest(language=language, screen=name):
                    columns = text_columns(system_screen.screen_image(name, language))
                    left, right = min(columns), 127 - max(columns)
                    self.assertGreaterEqual(left, system_screen.TEXT_MARGIN - 1)
                    self.assertGreaterEqual(right, system_screen.TEXT_MARGIN - 1)
                    self.assertLessEqual(abs(left - right), 3)

    def test_start_and_safe_wording(self):
        self.assertEqual(system_screen.screen_text('start', 'de'), 'Wird gestartet…')
        self.assertEqual(system_screen.screen_text('start', 'en'), 'Starting…')
        self.assertEqual(system_screen.screen_text('safe', 'de'),
                         'Kann jetzt ausgeschaltet werden')

    def test_oled_start_splash_is_localized_for_standard_artwork(self):
        german = bytes(start_splash(str(DEFAULT_LOGO), 'de').data)
        english = bytes(start_splash(str(DEFAULT_LOGO), 'en').data)
        plain = bytes(load_splash(DEFAULT_LOGO).data)
        self.assertNotEqual(german, english)
        self.assertNotEqual(german, plain)

    def test_custom_splash_png_is_used_unchanged(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'custom.png'
            Image.new('L', (128, 128), 200).save(path)
            self.assertEqual(bytes(start_splash(str(path), 'de').data),
                             bytes(load_splash(path).data))

    def test_firmware_splash_defaults_to_localized_start_screen(self):
        source = (ROOT / 'tools/build_ssd1327_splash.py').read_text(encoding='utf-8')
        self.assertIn("nargs='?'", source)
        self.assertIn("screen_image('start'", source)


class OledStopOrderTests(unittest.TestCase):
    def test_oled_stops_before_player(self):
        unit = (ROOT / 'systemd/sonoscdplayer-oled.service').read_text(encoding='utf-8')
        self.assertIn('After=local-fs.target sonoscdplayer-player.service', unit)

    def test_rendered_oled_unit_keeps_player_ordering(self):
        import render_runtime_units
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / 'src'
            (checkout / 'integrations').mkdir(parents=True)
            (checkout / 'player_service.py').write_text('', encoding='utf-8')
            units = render_runtime_units.render(
                ROOT / 'systemd', checkout=checkout,
                python_venv=Path(directory) / 'venv/bin/python', user='sonoscd')
        self.assertIn('sonoscdplayer-player.service',
                      [line for line in units['sonoscdplayer-oled.service'].splitlines()
                       if line.startswith('After=')][0])


if __name__ == '__main__':
    unittest.main()
