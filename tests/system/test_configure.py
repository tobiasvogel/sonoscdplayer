"""Offline tests for conservative configuration wizard helpers."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('configure', ROOT / 'tools/configure.py')
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


class ConfigureTests(unittest.TestCase):
    def test_ram_recommendations(self):
        self.assertEqual(config.suggested_buffer(1024), 102)
        self.assertEqual(config.suggested_buffer(512), 51)
        self.assertEqual(config.suggested_buffer(64), 16)
        self.assertEqual(config.suggested_buffer(8192), 512)

    def test_memtotal_not_available(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'meminfo'
            path.write_text('MemTotal: 2097152 kB\nMemAvailable: 1024 kB\n')
            self.assertEqual(config.total_ram_mib(path), 2048)

    def test_preserve_other_sections_and_comments(self):
        raw = ('[streaming]\nmax_buffer_mib = 16  # existing comment\n'
               '[metadata]\nonline_lookup = true\n[sonos]\nuse_queue = true\n'
               '[playback]\ncontinue = true\n')
        result = config.replace_values(raw, {('streaming', 'max_buffer_mib'): 128,
                                             ('metadata', 'online_lookup'): False})
        self.assertIn('max_buffer_mib = 128  # existing comment', result)
        self.assertIn('online_lookup = false', result)
        self.assertIn('[sonos]\nuse_queue = true', result)

    def test_missing_key_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'missing keys'):
            config.replace_values('[streaming]\n', {('streaming', 'max_buffer_mib'): 64})

    def test_unrelated_values_not_rewritten(self):
        raw = '[other]\nmax_buffer_mib = 5\n[streaming]\nmax_buffer_mib = 16\n'
        self.assertIn('[other]\nmax_buffer_mib = 5', config.replace_values(
            raw, {('streaming', 'max_buffer_mib'): 100}))


if __name__ == '__main__':
    unittest.main()
