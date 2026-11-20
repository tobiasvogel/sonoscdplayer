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
        replies = iter(['1', '', 'k', '2', 'k', 'k', 'n', 'k', 'k', 'k', '7', 'y'])
        result = config.interactive_choices(current, 205,
                    read=lambda _: next(replies), out=lambda _: None)
        self.assertEqual(result, {('streaming', 'max_buffer_mib'): 205,
                                  ('metadata', 'online_lookup'): False})

    def test_returning_to_section_reverts_change(self):
        current = config.existing_values(
            {'streaming': {'max_buffer_mib': 16}},
            __import__('tomllib').loads(config.REFERENCE.read_text()))
        replies = iter(['1', '', 'k', '1', 'k', 'k', '7', 'y'])
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
        replies = iter(['7', '', '0'])
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
        replies = iter(['4', 'n', 'k', 'k', '120', '7', '4', 'n', 'k', 'k', 'off', '7', 'y'])
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

    def test_m3_metadata_defaults_are_not_written_implicitly(self):
        import tomllib
        raw = '[streaming]\nmax_buffer_mib = 16\n'
        reference = tomllib.loads(config.REFERENCE.read_text())
        current = config.existing_values(tomllib.loads(raw), reference)
        self.assertTrue(current[('metadata', 'read_cd_text')])
        self.assertTrue(current[('metadata', 'auto_match')])
        self.assertEqual(current[('metadata.musicbrainz', 'timeout_seconds')], 8)
        self.assertEqual(current[('metadata.matching', 'confidence_threshold')], 90)
        self.assertEqual(config.replace_values(raw, config.pending_changes(current, current)), raw)

    def test_m3_matching_ranges_and_musicbrainz_timeout(self):
        import tomllib
        reference = tomllib.loads(config.REFERENCE.read_text())
        for section, key, value in (
                ('metadata.musicbrainz', 'timeout_seconds', 31),
                ('metadata.matching', 'minimum_track_titles', 0),
                ('metadata.matching', 'minimum_margin', -1),
                ('metadata.matching', 'confidence_threshold', True),
                ('metadata', 'read_cd_text', 'yes')):
            with self.subTest(field=(section, key)), self.assertRaisesRegex(ValueError, 'invalid'):
                parsed = {'streaming': {'max_buffer_mib': 16}}
                group = parsed
                for part in section.split('.'):
                    group = group.setdefault(part, {})
                group[key] = value
                config.existing_values(parsed, reference)

    def test_m3_existing_parent_and_missing_nested_table(self):
        import tomllib
        raw = ('[metadata]\n# preserve this\nonline_lookup = true\n'
               '[streaming]\nmax_buffer_mib = 16\n')
        updated = config.replace_values(raw, {
            ('metadata.matching', 'confidence_threshold'): 94,
            ('metadata.musicbrainz', 'timeout_seconds'): 12})
        cfg = tomllib.loads(updated)
        self.assertEqual(cfg['metadata']['matching']['confidence_threshold'], 94)
        self.assertEqual(cfg['metadata']['musicbrainz']['timeout_seconds'], 12)
        self.assertIn('# preserve this', updated)

    def test_m3_missing_parent_and_multiple_nested_tables(self):
        import tomllib
        raw = '[streaming]\nmax_buffer_mib = 16\n'
        updated = config.replace_values(raw, {
            ('metadata', 'enabled'): False,
            ('metadata.matching', 'minimum_margin'): 20,
            ('metadata.musicbrainz', 'enabled'): False})
        cfg = tomllib.loads(updated)
        self.assertFalse(cfg['metadata']['enabled'])
        self.assertFalse(cfg['metadata']['musicbrainz']['enabled'])
        self.assertEqual(cfg['metadata']['matching']['minimum_margin'], 20)

    def test_m3_menu_metadata_and_advanced_review(self):
        import tomllib
        current = config.existing_values(
            {'streaming': {'max_buffer_mib': 16}},
            tomllib.loads(config.REFERENCE.read_text()))
        answers = iter(['2', 'k', 'n', 'k', 'k', 'k', 'k',
                        '6', '95', 'k', 'k', '7', 'y'])
        changes = config.interactive_choices(current, 205,
                    read=lambda _: next(answers), out=lambda _: None)
        self.assertEqual(changes, {('metadata', 'read_cd_text'): False,
                                   ('metadata.matching', 'confidence_threshold'): 95})

    def test_m3_preserves_existing_matching_customizations(self):
        import tomllib
        raw = ('[metadata.matching]\nsoft_suffixes = ["Sonderedition"]\n'
               'confidence_threshold = 90  # inline\n')
        updated = config.replace_values(raw, {('metadata.matching', 'confidence_threshold'): 95})
        cfg = tomllib.loads(updated)
        self.assertEqual(cfg['metadata']['matching']['soft_suffixes'], ['Sonderedition'])
        self.assertIn('confidence_threshold = 95  # inline', updated)


class CoverWizardTests(unittest.TestCase):
    def test_cover_defaults_and_missing_file_are_virtual(self):
        import tomllib
        reference = tomllib.loads(config.UI_REFERENCE.read_text())
        values = config.existing_ui_values({}, reference)
        self.assertEqual(values[('ui.cover', 'mode')], 'auto')
        self.assertEqual(values[('ui.cover', 'max_dimension_px')], 512)
        self.assertEqual(values[('ui.cover', 'jpeg_quality')], 85)
        self.assertEqual(values[('ui.cover', 'budget_mb')], 100)
        self.assertEqual(config.replace_values('[ui]\nlanguage = "de"\n', {}),
                         '[ui]\nlanguage = "de"\n')

    def test_cover_bounds_and_boolean_rejected(self):
        import tomllib
        ref = tomllib.loads(config.UI_REFERENCE.read_text())
        for key, value in (('max_dimension_px', 63), ('max_dimension_px', 2049),
                           ('jpeg_quality', 39), ('jpeg_quality', 96),
                           ('budget_mb', 0), ('budget_mb', 10001),
                           ('budget_mb', True), ('mode', 'automatic')):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'invalid'):
                config.existing_ui_values({'ui': {'cover': {key: value}}}, ref)

    def test_cover_input_validates_choices(self):
        output = []
        answers = iter(['manual', '?', 'off'])
        self.assertEqual(config.ask('Cover', 'auto', 'auto', 'cover_mode',
                                    read=lambda _: next(answers), out=output.append,
                                    help_text='help about covers'), 'off')
        self.assertIn('help about covers', output)
        self.assertEqual(config.ask('Cover', 'auto', 'auto', 'cover_mode',
                                    read=lambda _: 'k'), 'auto')

    def test_cover_update_preserves_ui_language_and_comments(self):
        import tomllib
        raw = ('# original\n[ui]\nlanguage = "de"\n'
               '[ui.cover]\nmode = "auto" # keep this comment\n'
               'jpeg_quality = 85\n[ui.text_input]\nextra_characters = "äöü"\n')
        updated = config.replace_values(raw, {
            ('ui.cover', 'mode'): 'off', ('ui.cover', 'budget_mb'): 250})
        decoded = tomllib.loads(updated)
        self.assertEqual(decoded['ui']['language'], 'de')
        self.assertEqual(decoded['ui']['cover']['mode'], 'off')
        self.assertEqual(decoded['ui']['cover']['budget_mb'], 250)
        self.assertIn('# keep this comment', updated)
        self.assertIn('extra_characters = "äöü"', updated)

    def test_cover_missing_nested_section_is_valid_toml(self):
        import tomllib
        raw = '[ui]\nlanguage = "en"\n[ui.text_input]\nextra_characters = "ä"\n'
        updated = config.replace_values(raw, {('ui.cover', 'mode'): 'on'})
        decoded = tomllib.loads(updated)
        self.assertEqual(decoded['ui']['cover']['mode'], 'on')
        self.assertEqual(decoded['ui']['language'], 'en')

    def test_cover_menu_and_review(self):
        import tomllib
        current = config.existing_values({'streaming': {'max_buffer_mib': 16}},
                                         tomllib.loads(config.REFERENCE.read_text()))
        ui_current = config.existing_ui_values({},
                                               tomllib.loads(config.UI_REFERENCE.read_text()))
        replies = iter(['7', 'off', 'k', '75', '200', '9', 'y'])
        changes = config.interactive_choices(current, 205, ui_current=ui_current,
                    read=lambda _: next(replies), out=lambda _: None)
        self.assertEqual(changes, {('ui.cover', 'mode'): 'off',
                                   ('ui.cover', 'jpeg_quality'): 75,
                                   ('ui.cover', 'budget_mb'): 200})

    def test_ui_plan_uses_both_services_and_ui_reference(self):
        player = Path('/etc/sonoscdplayer/player.toml')
        ui = Path('/etc/sonoscdplayer/ui.toml')
        path, updated, ref, units = config.plan_update(
            {('ui.cover', 'mode'): 'off'},
            {('streaming', 'max_buffer_mib'): 16}, {('ui.cover', 'mode'): 'auto'},
            player_path=player, ui_path=ui,
            raw='[streaming]\nmax_buffer_mib = 16\n',
            ui_raw='[ui]\nlanguage = "de"\n')
        self.assertEqual(path, ui)
        self.assertEqual(ref, config.UI_REFERENCE)
        self.assertEqual(units, list(config.UI_SERVICES))
        self.assertIn('mode = "off"', updated)

    def test_refuse_mixed_file_update_and_missing_ui(self):
        kwargs = {'player_path': Path('/config/player.toml'),
                  'ui_path': Path('/config/ui.toml'),
                  'raw': '[streaming]\nmax_buffer_mib = 16\n',
                  'ui_raw': '[ui]\nlanguage = "de"\n'}
        with self.assertRaisesRegex(ValueError, 'one file per run'):
            config.plan_update({('streaming', 'max_buffer_mib'): 100,
                                ('ui.cover', 'mode'): 'off'},
                               {('streaming', 'max_buffer_mib'): 16},
                               {('ui.cover', 'mode'): 'auto'}, **kwargs)
        with self.assertRaisesRegex(ValueError, 'install ui.toml'):
            config.plan_update({('ui.cover', 'mode'): 'off'},
                               {('streaming', 'max_buffer_mib'): 16},
                               {('ui.cover', 'mode'): 'auto'}, **{**kwargs, 'ui_raw': None})

    def test_mixed_changes_warn_in_review_and_allow_revert(self):
        import tomllib
        current = config.existing_values({'streaming': {'max_buffer_mib': 16}},
                                         tomllib.loads(config.REFERENCE.read_text()))
        ui_current = config.existing_ui_values({},
                                               tomllib.loads(config.UI_REFERENCE.read_text()))
        # First change the buffer, then the cover mode. Review refuses mixing;
        # return to streaming, use k to revert, then confirm the UI-only change.
        answers = iter(['1', '', 'k', '7', 'off', 'k', 'k', 'k', '9',
                        '1', 'k', 'k', '9', 'y'])
        output = []
        changes = config.interactive_choices(current, 205, ui_current=ui_current,
                   read=lambda _: next(answers), out=output.append)
        self.assertEqual(changes, {('ui.cover', 'mode'): 'off'})
        self.assertIn('Apply one file', '\n'.join(output))

    def test_cover_apply_delegates_to_config_swap_and_checks_both_services(self):
        from unittest.mock import patch
        import tomllib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            player, ui = root / 'player.toml', root / 'ui.toml'
            player.write_text('[streaming]\nmax_buffer_mib = 16\n')
            ui.write_text('[ui]\nlanguage = "de"\n')
            calls = []
            class StubSwap:
                def swap(self, current, new, **kwargs):
                    calls.append((current, tomllib.loads(new.read_text()), kwargs))
                    return 0
            with (patch.object(config.os, 'geteuid', return_value=0),
                  patch.object(config.sys.stdin, 'isatty', return_value=True),
                  patch.object(config, 'interactive_choices', return_value={('ui.cover', 'mode'): 'off'}),
                  patch.object(config, 'load_swap', return_value=StubSwap()),
                  patch('builtins.input', return_value='APPLY')):
                self.assertEqual(config.main(['--config', str(player), '--apply']), 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], ui)
            self.assertEqual(calls[0][1]['ui']['cover']['mode'], 'off')
            self.assertEqual(calls[0][2]['services'], list(config.UI_SERVICES))
            self.assertEqual(calls[0][2]['reference'], config.UI_REFERENCE)
            self.assertTrue(calls[0][2]['apply'])
            # Stub does not apply; wizard must not write either config directly.
            self.assertNotIn('[ui.cover]', ui.read_text())

    def test_cover_preview_never_calls_swap_or_writes(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            player, ui = root / 'player.toml', root / 'ui.toml'
            player.write_text('[streaming]\nmax_buffer_mib = 16\n')
            ui.write_text('[ui]\nlanguage = "en"\n')
            with (patch.object(config.sys.stdin, 'isatty', return_value=True),
                  patch.object(config, 'interactive_choices', return_value={('ui.cover', 'mode'): 'on'}),
                  patch.object(config, 'load_swap', side_effect=AssertionError('called'))):
                self.assertEqual(config.main(['--config', str(player)]), 0)
            self.assertEqual(ui.read_text(), '[ui]\nlanguage = "en"\n')


class DisplayUiWizardTests(unittest.TestCase):
    def test_ui_language_and_extra_character_defaults(self):
        import tomllib
        ref = tomllib.loads(config.UI_REFERENCE.read_text(encoding='utf-8'))
        values = config.existing_ui_values({}, ref)
        self.assertEqual(values[('ui', 'language')], 'de')
        self.assertEqual(values[('ui.text_input', 'extra_characters')], 'äöüÄÖÜß')

    def test_ui_language_and_extra_characters_reject_invalid_values(self):
        import tomllib
        ref = tomllib.loads(config.UI_REFERENCE.read_text(encoding='utf-8'))
        for group, key, value in (
            ('ui', 'language', 'fr'),
            ('ui', 'language', True),
            ('ui.text_input', 'extra_characters', 10),
            ('ui.text_input', 'extra_characters', 'a'),
            ('ui.text_input', 'extra_characters', 'ää'),
            ('ui.text_input', 'extra_characters', 'ä '),
            ('ui.text_input', 'extra_characters', '界' * 65),
        ):
            nested = {'ui': {key: value}} if group == 'ui' else {
                'ui': {'text_input': {key: value}}}
            with self.subTest(nested=nested), self.assertRaisesRegex(ValueError, 'invalid'):
                config.existing_ui_values(nested, ref)

    def test_ui_language_navigation_and_review(self):
        import tomllib
        current = config.existing_values({'streaming': {'max_buffer_mib': 16}},
                                         tomllib.loads(config.REFERENCE.read_text()))
        ui_current = config.existing_ui_values({}, tomllib.loads(config.UI_REFERENCE.read_text()))
        replies = iter(['8', 'en', 'k', '9', 'y'])
        changes = config.interactive_choices(current, 205, ui_current=ui_current,
                   read=lambda _: next(replies), out=lambda _: None)
        self.assertEqual(changes, {('ui', 'language'): 'en'})

    def test_extra_characters_escaping_and_existing_comments(self):
        import tomllib
        raw = ('[ui]\nlanguage = "de"  # keep\n'
               '[ui.text_input]\nextra_characters = "äöüÄÖÜß"  # keep too\n'
               '[ui.cover]\nmode = "auto"\n')
        updated = config.replace_values(raw, {
            ('ui', 'language'): 'en',
            ('ui.text_input', 'extra_characters'): 'àé#"\\',
        })
        values = tomllib.loads(updated)
        self.assertEqual(values['ui']['language'], 'en')
        self.assertEqual(values['ui']['text_input']['extra_characters'], 'àé#"\\')
        self.assertIn('# keep too', updated)
        self.assertEqual(values['ui']['cover']['mode'], 'auto')

    def test_ui_update_targets_only_ui_config(self):
        from pathlib import Path
        raw = '[ui]\nlanguage = "de"\n'
        player = '[streaming]\nmax_buffer_mib = 16\n'
        result = config.plan_update({('ui', 'language'): 'en'},
                                    {('streaming', 'max_buffer_mib'): 16},
                                    {('ui', 'language'): 'de'},
                                    player_path=Path('/tmp/player.toml'),
                                    ui_path=Path('/tmp/ui.toml'), raw=player, ui_raw=raw)
        path, updated, reference, services = result
        self.assertEqual(path.name, 'ui.toml')
        self.assertEqual(reference, config.UI_REFERENCE)
        self.assertEqual(services, list(config.UI_SERVICES))
        self.assertEqual(config.replace_values(raw, {}), raw)
        self.assertIn('language = "en"', updated)

    def test_saved_language_warning_on_preview(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'player.toml').write_text('[streaming]\nmax_buffer_mib = 16\n')
            (folder / 'ui.toml').write_text('[ui]\nlanguage = "de"\n')
            out = io.StringIO()
            with (patch.object(config.sys.stdin, 'isatty', return_value=True),
                  patch.object(config, 'interactive_choices', return_value={('ui', 'language'): 'en'}),
                  patch.object(config, 'load_swap', side_effect=AssertionError('preview tried to swap')),
                  redirect_stdout(out)):
                self.assertEqual(config.main(['--config', str(folder / 'player.toml')]), 0)
            self.assertIn('ui.json takes precedence', out.getvalue())
            self.assertEqual((folder / 'ui.toml').read_text(), '[ui]\nlanguage = "de"\n')

if __name__ == '__main__':
    unittest.main()
