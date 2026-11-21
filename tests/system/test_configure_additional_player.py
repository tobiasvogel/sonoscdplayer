"""M4b.11: safe additional player fields without changing menu numbers."""
import importlib.util
from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('configure_m4b11', ROOT / 'tools/configure.py')
wizard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wizard)


class AdditionalPlayerSettingsTests(unittest.TestCase):
    def setUp(self):
        self.reference = tomllib.loads(wizard.REFERENCE.read_text(encoding='utf-8'))
        self.parsed = {'streaming': {'max_buffer_mib': 16}}

    def test_reference_defaults_for_new_fields(self):
        values = wizard.existing_values(self.parsed, self.reference)
        self.assertEqual(values[('sonos', 'album_prefix')], 'CD: ')
        self.assertEqual(values[('service', 'shutdown_policy')], 'stop_owned')

    def test_invalid_existing_values_rejected(self):
        for section, key, bad in (('service', 'shutdown_policy', 'invalid'),
                                  ('sonos', 'album_prefix', 'x\nunsafe'),
                                  ('sonos', 'album_prefix', 'x' * 65)):
            with self.subTest(section=section, key=key):
                config = {**self.parsed, section: {key: bad}}
                with self.assertRaisesRegex(ValueError, 'invalid'):
                    wizard.existing_values(config, self.reference)

    def test_prefix_preserves_trailing_space_and_clears_explicitly(self):
        self.assertEqual(wizard.ask('Prefix', 'CD: ', 'CD: ', 'album_prefix',
                                    read=lambda _: 'CD player: ', out=lambda _: None),
                         'CD player: ')
        self.assertEqual(wizard.ask('Prefix', 'CD: ', 'CD: ', 'album_prefix',
                                    read=lambda _: '-', out=lambda _: None), '')

    def test_policy_only_accepts_known_choices(self):
        answers = iter(['dangerous', 'leave_playing'])
        self.assertEqual(wizard.ask('Policy', 'stop_owned', 'stop_owned',
                                    'shutdown_policy', read=lambda _: next(answers),
                                    out=lambda _: None), 'leave_playing')

    def test_letter_group_keeps_existing_menu_numbers(self):
        self.assertEqual(wizard.PLAYER_ADVANCED_GROUP[1],
                         (('sonos', 'album_prefix'), ('service', 'shutdown_policy')))
        self.assertEqual(len(wizard.MENU_GROUPS), 6)

    def test_advanced_menu_stages_both_fields_without_apply(self):
        current = wizard.existing_values(self.parsed, self.reference)
        answers = iter(['a', 'Tape: ', 'leave_playing', '7', 'y'])
        changes = wizard.interactive_choices(
            current, 100, parsed=self.parsed,
            read=lambda _: next(answers), out=lambda _: None)
        self.assertEqual(changes, {
            ('sonos', 'album_prefix'): 'Tape: ',
            ('service', 'shutdown_policy'): 'leave_playing'})

    def test_preview_rewrites_only_selected_field(self):
        original = '[streaming]\nmax_buffer_mib = 16\n[sonos]\nalbum_prefix = "CD: " # original\n'
        updated = wizard.replace_values(original, {('sonos', 'album_prefix'): 'Disc: '})
        self.assertIn('album_prefix = "Disc: " # original', updated)
        self.assertEqual(tomllib.loads(updated)['sonos']['album_prefix'], 'Disc: ')

    def test_existing_confirmation_review_includes_new_fields(self):
        old = wizard.existing_values(self.parsed, self.reference)
        changes = wizard.review(old, {**old, ('service', 'shutdown_policy'): 'leave_playing'},
                                out=lambda _: None)
        self.assertEqual(changes, {('service', 'shutdown_policy'): 'leave_playing'})


if __name__ == '__main__':
    unittest.main()
