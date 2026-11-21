"""M4b.14: metadata lists use the runtime matching-policy validator."""
import importlib.util
from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('configure_matching_test', ROOT/'tools/configure.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)

class MatchingWizardTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT/'config/player.example.toml').read_text()
        self.current = config.existing_values(tomllib.loads(self.raw), tomllib.loads(self.raw))

    def plan(self, changes):
        return config.plan_update(changes, self.current, {},
                                  player_path=ROOT/'player.toml', ui_path=ROOT/'ui.toml',
                                  raw=self.raw, ui_raw=None)

    def test_json_list_input_and_toml_output(self):
        result = config.ask('Rules', [], [], 'string_list',
                            read=lambda _: '["de", "fr"]')
        self.assertEqual(result, ['de', 'fr'])
        self.assertEqual(tomllib.loads('a = ' + config.render_value(result))['a'], result)

    def test_valid_language_packs_accepted(self):
        _, updated, _, services = self.plan({('metadata.matching', 'rule_languages'): ['de', 'fr']})
        self.assertEqual(tomllib.loads(updated)['metadata']['matching']['rule_languages'], ['de', 'fr'])
        self.assertEqual(services, ['sonoscdplayer-player.service'])

    def test_unknown_pack_rejected(self):
        with self.assertRaises(ValueError):
            self.plan({('metadata.matching', 'rule_languages'): ['zz']})

    def test_duplicate_and_overlap_rejected(self):
        with self.assertRaises(ValueError):
            self.plan({('metadata.matching', 'soft_suffixes'): ['Live', 'Live']})
        with self.assertRaises(ValueError):
            self.plan({('metadata.matching', 'soft_suffixes'): ['Live']})

    def test_list_editor_exposes_only_matching_lists(self):
        self.assertEqual(len(config.MATCHING_MENU_GROUP[1]), 3)

if __name__ == '__main__':
    unittest.main()
