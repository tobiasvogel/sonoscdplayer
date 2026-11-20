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
        with self.assertRaisesRegex(ValueError, 'missing required'):
            config.replace_values('[streaming]\n', {('streaming', 'max_buffer_mib'): 64})

    def test_missing_optional_sections_use_reference_defaults(self):
        parsed = {'streaming': {'max_buffer_mib': 16}}
        reference = {'metadata': {'online_lookup': True},
                     'sonos': {'use_queue': True},
                     'playback': {'continue': True}}
        values = config.existing_values(parsed, reference)
        self.assertEqual(values[('metadata', 'online_lookup')], True)
        self.assertEqual(values[('streaming', 'max_buffer_mib')], 16)

    def test_missing_required_buffer_fails_with_clear_error(self):
        with self.assertRaisesRegex(ValueError, 'missing required'):
            config.existing_values({}, {})

    def test_add_missing_optional_section_only_when_changed(self):
        raw = '[streaming]\nmax_buffer_mib = 16\n'
        changed = config.replace_values(raw, {('metadata', 'online_lookup'): False})
        import tomllib
        self.assertEqual(tomllib.loads(changed)['metadata']['online_lookup'], False)
        self.assertEqual(config.replace_values(raw, {}), raw)

    def test_add_key_before_nested_subsection(self):
        raw = '[metadata.matching]\nminimum_margin = 15\n'
        updated = config.replace_values(raw, {('metadata', 'online_lookup'): False})
        import tomllib
        self.assertFalse(tomllib.loads(updated)['metadata']['online_lookup'])
        self.assertEqual(tomllib.loads(updated)['metadata']['matching']['minimum_margin'], 15)

    def test_add_key_to_existing_section_preserving_comments(self):
        raw = '[metadata]\n# keep me\nenabled = true\n[sonos]\nuse_queue = true\n'
        updated = config.replace_values(raw, {('metadata', 'online_lookup'): False})
        import tomllib
        self.assertIn('# keep me', updated)
        self.assertFalse(tomllib.loads(updated)['metadata']['online_lookup'])
        self.assertTrue(tomllib.loads(updated)['sonos']['use_queue'])

    def test_unrelated_values_not_rewritten(self):
        raw = '[other]\nmax_buffer_mib = 5\n[streaming]\nmax_buffer_mib = 16\n'
        self.assertIn('[other]\nmax_buffer_mib = 5', config.replace_values(
            raw, {('streaming', 'max_buffer_mib'): 100}))

    def test_keep_current_or_accept_recommendation(self):
        responses = iter(['?', 'k'])
        output = []
        self.assertEqual(config.ask('Buffer', 16, 205, 'int', help_text='PCM help',
                                    read=lambda _: next(responses), out=output.append), 16)
        self.assertIn('PCM help', output)
        self.assertEqual(config.ask('Buffer', 16, 205, 'int', read=lambda _: ''), 205)

    def test_section_navigation_and_review(self):
        current = {('streaming', 'max_buffer_mib'): 16,
                   ('metadata', 'online_lookup'): True,
                   ('sonos', 'use_queue'): True,
                   ('playback', 'continue'): True}
        replies = iter(['1', '', 'k', '2', 'n', '4', 'y'])
        result = config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=lambda _: None)
        self.assertEqual(result, {('streaming', 'max_buffer_mib'): 205,
                                  ('metadata', 'online_lookup'): False})

    def test_returning_to_section_reverts_change(self):
        current = {('streaming', 'max_buffer_mib'): 16,
                   ('metadata', 'online_lookup'): True,
                   ('sonos', 'use_queue'): True,
                   ('playback', 'continue'): True}
        replies = iter(['1', '', 'k', '1', 'k', 'k', '4', 'y'])
        result = config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=lambda _: None)
        self.assertEqual(result, {})

    def test_exit_discard_changes(self):
        current = {('streaming', 'max_buffer_mib'): 16,
                   ('metadata', 'online_lookup'): True,
                   ('sonos', 'use_queue'): True,
                   ('playback', 'continue'): True}
        replies = iter(['1', '', 'k', '0'])
        self.assertIsNone(config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=lambda _: None))

    def test_review_must_be_confirmed(self):
        current = {('streaming', 'max_buffer_mib'): 16,
                   ('metadata', 'online_lookup'): True,
                   ('sonos', 'use_queue'): True,
                   ('playback', 'continue'): True}
        replies = iter(['4', '', '0'])
        self.assertIsNone(config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=lambda _: None))



if __name__ == '__main__':
    unittest.main()
