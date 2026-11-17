"""Cover preferences: one configuration source and clear auto/on/off behavior."""
import tempfile
from pathlib import Path
import unittest

from cover_preferences import CoverPreferences, read_cover_preferences


class CoverPreferencesTests(unittest.TestCase):
    def test_missing_ui_config_keeps_auto_default(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = read_cover_preferences(Path(directory) / 'missing.toml')
        self.assertEqual(settings.mode, 'auto')
        self.assertEqual(settings.max_dimension_px, 512)

    def test_modes_are_distinct(self):
        for mode, capable, expected in (
            ('auto', False, False), ('auto', True, True),
            ('on', False, True), ('on', True, True),
            ('off', False, False), ('off', True, False),
        ):
            with self.subTest(mode=mode, capable=capable):
                policy = CoverPreferences(mode=mode).download_policy(
                    display_supports_cover=capable)
                self.assertEqual(policy.enabled, expected)

    def test_reads_ui_toml_with_one_dimension(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'ui.toml'
            filename.write_text('[ui.cover]\nmode = "on"\nmax_dimension_px = 640\n',
                                encoding='utf-8')
            settings = read_cover_preferences(filename)
        self.assertTrue(settings.download_policy(display_supports_cover=False).enabled)
        self.assertEqual(settings.max_dimension_px, 640)

    def test_rejects_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'ui.toml'
            for data in ('[ui.cover]\nmode = "invalid"\n',
                         '[ui.cover]\nmode = true\n',
                         '[ui.cover]\nmax_dimension_px = 12\n',
                         '[ui.cover]\nmax_dimension_px = true\n',
                         'ui = "invalid"\n',
                         '[ui]\ncover = "invalid"\n'):
                with self.subTest(data=data):
                    filename.write_text(data, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        read_cover_preferences(filename)

    def test_mode_off_cannot_be_overridden_by_capability(self):
        prefs = CoverPreferences(mode='off', max_dimension_px=512)
        self.assertFalse(prefs.download_policy(display_supports_cover=True).enabled)
        with self.assertRaises(ValueError):
            prefs.download_policy(display_supports_cover='yes')


if __name__ == '__main__':
    unittest.main()
