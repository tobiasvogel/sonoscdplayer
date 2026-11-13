"""Localized, centered shutdown/reboot frames (build-time, offline)."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import build_shutdown_renderer as builder  # noqa: E402
from sonos_ui.i18n import available_languages  # noqa: E402

ARTWORK = {
    'wait': 'shutdown_screen.png', 'transition': 'shutdown_screen.png',
    'reboot': 'reboot_screen.png', 'final': 'pleasewait_screen.png',
    'safe': 'safetounplug_screen.png',
}


def unpack(frame):
    pixels = []
    for value in frame:
        pixels.extend((value >> 4, value & 15))
    return pixels


def text_columns(frame):
    pixels = unpack(frame)
    return [x for x in range(128)
            if any(pixels[y * 128 + x] > 2 for y in range(builder.ICON_BOTTOM, 128))]


class LocalizedShutdownFrameTests(unittest.TestCase):
    def frame(self, name, language):
        return builder.compose_frame(ROOT / 'src/data' / ARTWORK[name],
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
                    columns = text_columns(frame)
                    self.assertTrue(columns)
                    left, right = min(columns), 127 - max(columns)
                    self.assertGreaterEqual(left, builder.TEXT_MARGIN - 1)
                    self.assertGreaterEqual(right, builder.TEXT_MARGIN - 1)
                    self.assertLessEqual(abs(left - right), 3)

    def test_old_english_artwork_text_is_removed(self):
        pixels = unpack(self.frame('reboot', 'de'))
        english = unpack(builder.pack_ssd1327(ROOT / 'src/data/reboot_screen.png'))
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
        self.assertEqual(builder.TEXT_FONT.parent, ROOT / 'src' / 'fonts')
        license_text = (ROOT / 'src/fonts/LICENSE-EncodeSans-OFL').read_text(encoding='utf-8')
        self.assertIn('SIL OPEN FONT LICENSE Version 1.1', license_text)

    def test_overlong_text_fails_the_build(self):
        with self.assertRaises(ValueError):
            builder.layout_text('Donaudampfschifffahrtsgesellschaftskapitän')


if __name__ == '__main__':
    unittest.main()
