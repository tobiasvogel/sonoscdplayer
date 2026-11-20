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
        import tomllib
        reference = tomllib.loads(config.REFERENCE.read_text())
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
        current = config.existing_values(
            {'streaming': {'max_buffer_mib': 16}},
            __import__('tomllib').loads(config.REFERENCE.read_text()))
        replies = iter(['1', '', 'k', '2', 'n', '6', 'y'])
        result = config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=lambda _: None)
        self.assertEqual(result, {('streaming', 'max_buffer_mib'): 205,
                                  ('metadata', 'online_lookup'): False})

    def test_returning_to_section_reverts_change(self):
        current = config.existing_values(
            {'streaming': {'max_buffer_mib': 16}},
            __import__('tomllib').loads(config.REFERENCE.read_text()))
        replies = iter(['1', '', 'k', '1', 'k', 'k', '6', 'y'])
        result = config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=lambda _: None)
        self.assertEqual(result, {})

    def test_exit_discard_changes(self):
        current = config.existing_values(
            {'streaming': {'max_buffer_mib': 16}},
            __import__('tomllib').loads(config.REFERENCE.read_text()))
        replies = iter(['1', '', 'k', '0'])
        self.assertIsNone(config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=lambda _: None))

    def test_review_must_be_confirmed(self):
        current = config.existing_values(
            {'streaming': {'max_buffer_mib': 16}},
            __import__('tomllib').loads(config.REFERENCE.read_text()))
        replies = iter(['6', '', '0'])
        self.assertIsNone(config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=lambda _: None))


    def test_m2_current_values_and_missing_idle_runtime_default(self):
        import tomllib
        reference = tomllib.loads(config.REFERENCE.read_text())
        current = config.existing_values({'streaming': {'max_buffer_mib': 16}}, reference)
        self.assertEqual(current[('audio_startup', 'pcm_timeout_seconds')], 45)
        self.assertEqual(current[('drive_power', 'strategy')], 'auto')
        self.assertIsNone(current[('drive_power', 'idle_spin_down_seconds')])

    def test_m2_numeric_limits_and_strategy(self):
        for field, invalid in ((('audio_startup', 'pcm_timeout_seconds'), 121),
                               (('drive_power', 'keepalive_interval_seconds'), 2),
                               (('drive_power', 'idle_spin_down_seconds'), 29)):
            import tomllib
            reference = tomllib.loads(config.REFERENCE.read_text())
            group, key = field
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'invalid'):
                config.existing_values({'streaming': {'max_buffer_mib': 16},
                                        group: {key: invalid}}, reference)
        replies = iter(['invalid', 'keepalive'])
        self.assertEqual(config.ask('Strategy', 'auto', 'auto', 'strategy',
                                    choices=config.DRIVE_STRATEGIES,
                                    read=lambda _: next(replies), out=lambda _: None), 'keepalive')

    def test_m2_idle_off_deletes_setting_without_touching_other_values(self):
        import tomllib
        raw = ('[drive_power]\nenabled = true\nstrategy = "auto"\n'
               'idle_spin_down_seconds = 120 # comment\nkeepalive_interval_seconds = 10\n'
               '[audio_startup]\nprebuffer_before_sonos = true\n')
        changed = config.replace_values(raw, {
            ('drive_power', 'idle_spin_down_seconds'): None,
            ('drive_power', 'strategy'): 'keepalive',
            ('audio_startup', 'prebuffer_before_sonos'): False})
        doc = tomllib.loads(changed)
        self.assertNotIn('idle_spin_down_seconds', doc['drive_power'])
        self.assertEqual(doc['drive_power']['strategy'], 'keepalive')
        self.assertEqual(doc['drive_power']['keepalive_interval_seconds'], 10)
        self.assertFalse(doc['audio_startup']['prebuffer_before_sonos'])

    def test_m2_idle_dependencies(self):
        import tomllib
        current = config.existing_values(
            {'streaming': {'max_buffer_mib': 16}},
            tomllib.loads(config.REFERENCE.read_text()))
        current[('drive_power', 'idle_spin_down_seconds')] = 120
        for enabled, strategy in ((False, 'auto'), (True, 'off'), (True, 'start'),
                                  (True, 'power_condition')):
            candidate = {**current, ('drive_power', 'enabled'): enabled,
                         ('drive_power', 'strategy'): strategy}
            with self.subTest(strategy=strategy), self.assertRaisesRegex(ValueError, 'requires'):
                config.validate_combination(candidate)
        config.validate_combination({**current, ('drive_power', 'strategy'): 'keepalive'})
        with self.assertRaisesRegex(ValueError, 'overrides'):
            config.validate_combination(current, {'drive_power': {'mode_page_overrides': {'IACT': 1}}})

    def test_m2_navigation_and_validation_prevents_invalid_finish(self):
        import tomllib
        current = config.existing_values(
            {'streaming': {'max_buffer_mib': 16}},
            tomllib.loads(config.REFERENCE.read_text()))
        replies = iter(['4', 'n', 'k', 'k', '120', '6', '4', 'n', 'k', 'k', 'off', '6', 'y'])
        output = []
        result = config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=output.append)
        self.assertIn('Configuration conflict:', '\n'.join(output))
        self.assertEqual(result[('drive_power', 'enabled')], False)
        self.assertNotIn(('drive_power', 'idle_spin_down_seconds'), result)

    def test_m2_optional_sections_insert_and_toml_valid(self):
        import tomllib
        raw = '[streaming]\nmax_buffer_mib = 16\n'
        updated = config.replace_values(raw, {
            ('audio_startup', 'pcm_timeout_seconds'): 60,
            ('drive_power', 'strategy'): 'keepalive',
            ('drive_power', 'idle_spin_down_seconds'): 300})
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed['audio_startup']['pcm_timeout_seconds'], 60)
        self.assertEqual(parsed['drive_power']['strategy'], 'keepalive')
        self.assertEqual(parsed['drive_power']['idle_spin_down_seconds'], 300)



if __name__ == '__main__':
    unittest.main()
