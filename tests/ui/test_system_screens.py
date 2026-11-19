"""Localized start, shutdown and restart screens and OLED stop ordering."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
import build_shutdown_renderer as builder
from sonoscdplayer.ui import system_screens as system_screen
from sonoscdplayer.ui.i18n import available_languages
from sonoscdplayer.ui.display_service import DEFAULT_LOGO, load_splash, start_splash
ARTWORK = {
    'wait': 'shutdown_screen.png', 'transition': 'shutdown_screen.png',
    'reboot': 'reboot_screen.png', 'final': 'pleasewait_screen.png',
    'safe': 'safetounplug_screen.png',
}



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
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'ui.toml').write_text('[ui]\nlanguage = "en"\n', encoding='utf-8')
            out = Path(directory) / 'splash.bin'
            example = ROOT / 'config/player.example.toml'
            env = dict(os.environ, SONOSCDPLAYER_CONFIG_DIR=directory,
                       STATE_DIRECTORY=str(Path(directory) / 'state'))  # no menu choice
            result = subprocess.run(
                [sys.executable, str(ROOT / 'tools/build_ssd1327_splash.py'), '--output', str(out),
                 '--display-config', str(example)],
                env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            from build_ssd1327_splash import build, to_frame
            from sonoscdplayer.hardware.displays.pins import load_display_pins
            expected = build(to_frame(system_screen.screen_image('start', 'en')),
                             pins=load_display_pins(example))
            self.assertEqual(out.read_text(encoding='utf-8'), expected)


class OledStopOrderTests(unittest.TestCase):
    def test_oled_stops_before_player(self):
        unit = (ROOT / 'systemd/sonoscdplayer-oled.service').read_text(encoding='utf-8')
        self.assertIn('After=local-fs.target sonoscdplayer-player.service', unit)

    def test_rendered_oled_unit_keeps_player_ordering(self):
        import render_runtime_units
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / 'src'
            (checkout / 'sonoscdplayer' / 'player').mkdir(parents=True)
            (checkout / 'sonoscdplayer' / 'player' / 'service.py').write_text('', encoding='utf-8')
            units = render_runtime_units.render(
                ROOT / 'systemd', checkout=checkout,
                python_venv=Path(directory) / 'venv/bin/python', user='sonoscd')
        self.assertIn('sonoscdplayer-player.service',
                      [line for line in units['sonoscdplayer-oled.service'].splitlines()
                       if line.startswith('After=')][0])


def unpack(frame):
    pixels = []
    for value in frame:
        pixels.extend((value >> 4, value & 15))
    return pixels


def frame_text_columns(frame):
    pixels = unpack(frame)
    return [x for x in range(128)
            if any(pixels[y * 128 + x] > 2 for y in range(builder.ICON_BOTTOM, 128))]


class LocalizedShutdownFrameTests(unittest.TestCase):
    def frame(self, name, language):
        return builder.compose_frame(ROOT / 'src/sonoscdplayer/assets/screens' / ARTWORK[name],
                                     builder.frame_text(name, language))

    def test_text_is_localized(self):
        self.assertEqual(builder.frame_text('reboot', 'de'), 'Wird neu gestartet…')
        self.assertEqual(builder.frame_text('reboot', 'en'), 'Restarting…')
        self.assertEqual(builder.frame_text('wait', 'de'), 'Wird heruntergefahren…')
        self.assertEqual(builder.frame_text('final', 'en'), 'Please wait')
        self.assertNotEqual(self.frame('reboot', 'de'), self.frame('reboot', 'en'))

    def test_every_language_fits_with_margins_and_is_centered(self):
        for language in available_languages():
            for name in ARTWORK:
                with self.subTest(language=language, frame=name):
                    frame = self.frame(name, language)
                    self.assertEqual(len(frame), 8192)
                    columns = frame_text_columns(frame)
                    self.assertTrue(columns)
                    left, right = min(columns), 127 - max(columns)
                    self.assertGreaterEqual(left, builder.TEXT_MARGIN - 1)
                    self.assertGreaterEqual(right, builder.TEXT_MARGIN - 1)
                    self.assertLessEqual(abs(left - right), 3)

    def test_old_english_artwork_text_is_removed(self):
        pixels = unpack(self.frame('reboot', 'de'))
        english = unpack(builder.pack_ssd1327(ROOT / 'src/sonoscdplayer/assets/screens/reboot_screen.png'))
        top = slice(0, builder.ICON_BOTTOM * 128)
        self.assertEqual(pixels[top], english[top])
        self.assertNotEqual(pixels[top.stop:], english[top.stop:])

    def test_language_comes_from_ui_toml(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'ui.toml').write_text('[ui]\nlanguage = "en"\n', encoding='utf-8')
            old = os.environ.get('SONOSCDPLAYER_CONFIG_DIR')
            os.environ['SONOSCDPLAYER_CONFIG_DIR'] = directory
            try:
                self.assertEqual(builder.configured_language(), 'en')
            finally:
                if old is None:
                    os.environ.pop('SONOSCDPLAYER_CONFIG_DIR', None)
                else:
                    os.environ['SONOSCDPLAYER_CONFIG_DIR'] = old

    def test_bundled_font_and_license_are_present(self):
        self.assertTrue(builder.TEXT_FONT.is_file())
        self.assertEqual(builder.TEXT_FONT.parent, ROOT / 'src' / 'sonoscdplayer' / 'assets' / 'fonts')
        license_text = (ROOT / 'src/sonoscdplayer/assets/fonts/LICENSE-EncodeSans-OFL').read_text(encoding='utf-8')
        self.assertIn('SIL OPEN FONT LICENSE Version 1.1', license_text)

    def test_overlong_text_fails_the_build(self):
        with self.assertRaises(ValueError):
            builder.layout_text('Donaudampfschifffahrtsgesellschaftskapitän')



class MenuLanguageTests(unittest.TestCase):
    """the menu language is remembered and drives the shutdown frames."""

    def test_ui_state_round_trip_and_fallbacks(self):
        from sonoscdplayer.ui.language_state import UiState
        with tempfile.TemporaryDirectory() as directory:
            logs = []
            state = UiState(Path(directory) / 'state', log=logs.append)
            self.assertIsNone(state.language(('de', 'en')))
            self.assertTrue(state.save_language('en'))
            self.assertEqual(state.language(('de', 'en')), 'en')
            self.assertEqual(state.path.stat().st_mode & 0o777, 0o600)
            self.assertIsNone(state.language(('de',)))  # locale file removed meanwhile
            state.path.write_text('{"version": 2, "language": "en"}')
            self.assertIsNone(state.language(('de', 'en')))
            state.path.write_text('{')
            self.assertIsNone(state.language(('de', 'en')))

    def test_state_file_stays_readable_for_the_c_programs(self):
        import json
        from sonoscdplayer.ui.language_state import UiState
        with tempfile.TemporaryDirectory() as directory:
            state = UiState(directory, log=lambda message: None)
            state.save_language('en')
            text = state.path.read_text()
            self.assertEqual(json.loads(text), {'language': 'en', 'version': 1})
            self.assertLess(len(text), 512)  # C reads at most 511 bytes

    def test_menu_reports_a_confirmed_language_change_only(self):
        from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
        from sonoscdplayer.ui.menu.speakers.multi_target import Topology
        saved = []
        menu = InteractiveMenu(discover=lambda **kw: Topology(()), language='de',
                               on_language_change=saved.append)
        menu.active = True
        menu.section = "settings"
        menu.language_picker = True
        menu.language_focus = menu.languages.index('de')
        menu.event('encoder_sw', 'pressed', now=0.0)
        self.assertEqual(saved, [])  # unchanged language: nothing to store
        menu.active = True
        menu.section = "settings"
        menu.language_picker = True
        menu.language_focus = menu.languages.index('en')
        menu.event('encoder_sw', 'pressed', now=0.0)
        self.assertEqual(saved, ['en'])
        self.assertEqual(menu.language, 'en')

    def test_failing_persistence_does_not_break_the_menu(self):
        from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
        from sonoscdplayer.ui.menu.speakers.multi_target import Topology
        def broken(language):
            raise OSError('read-only')
        menu = InteractiveMenu(discover=lambda **kw: Topology(()), language='de',
                               on_language_change=broken)
        menu.active = True
        menu.section = "settings"
        menu.language_picker = True
        menu.language_focus = menu.languages.index('en')
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=0.0))
        self.assertEqual(menu.language, 'en')

    def test_new_locale_file_is_built_into_the_shutdown_frames(self):
        """Adding a locale file is enough; no language list to maintain."""
        from unittest import mock
        import build_shutdown_renderer as builder
        from sonoscdplayer.ui import i18n
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'shutdown_frames.h'
            data = ROOT / 'src/sonoscdplayer/assets/screens'
            with mock.patch.object(i18n, 'available_languages', return_value=('de', 'en', 'fr')):
                builder.generate(data / 'shutdown_screen.png', data / 'pleasewait_screen.png',
                                 data / 'reboot_screen.png', data / 'shutdown_screen.png',
                                 out, 'ssd1327', language='de')
            header = out.read_text()
        self.assertIn('shutdown_frame_reboot_fr[8192]', header)
        self.assertIn('{"fr", shutdown_frame_wait_fr,', header)
        self.assertIn('SHUTDOWN_FRAME_SET_COUNT 3u', header)

    def test_firmware_splash_prefers_the_menu_language(self):
        import subprocess
        from build_ssd1327_splash import build, to_frame
        from sonoscdplayer.hardware.displays.pins import load_display_pins
        from sonoscdplayer.ui.language_state import UiState
        example = ROOT / 'config/player.example.toml'
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'ui.toml').write_text('[ui]\nlanguage = "de"\n', encoding='utf-8')
            UiState(Path(directory) / 'state', log=lambda m: None).save_language('en')
            out = Path(directory) / 'splash.bin'
            env = dict(os.environ, SONOSCDPLAYER_CONFIG_DIR=directory,
                       STATE_DIRECTORY=str(Path(directory) / 'state'))
            result = subprocess.run(
                [sys.executable, str(ROOT / 'tools/build_ssd1327_splash.py'), '--output', str(out),
                 '--display-config', str(example)],
                env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(out.read_text(encoding='utf-8'),
                             build(to_frame(system_screen.screen_image('start', 'en')),
                                   pins=load_display_pins(example)))

    def test_oled_and_player_share_the_state_directory(self):
        for unit in ('sonoscdplayer-oled.service', 'sonoscdplayer-player.service'):
            with self.subTest(unit=unit):
                text = (ROOT / 'systemd' / unit).read_text()
                self.assertIn('StateDirectory=sonoscdplayer', text)
                self.assertIn('StateDirectoryMode=0700', text)

if __name__ == '__main__':
    unittest.main()
