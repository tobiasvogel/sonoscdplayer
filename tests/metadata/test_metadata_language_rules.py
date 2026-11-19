"""Data-only metadata language rules, no device or internet."""
from pathlib import Path
import tempfile
import unittest
from sonoscdplayer.metadata.language_rules import load_language_rules, normalize_key


class LanguageRuleTests(unittest.TestCase):
    def test_empty_default_is_language_neutral(self):
        rules = load_language_rules(())
        self.assertEqual(rules.languages, ())
        self.assertEqual(rules.normalize('Été'), 'ete')
        self.assertEqual(rules.normalize('東京'), '東京')

    def test_latin_accents_and_non_latin_marks(self):
        self.assertEqual(normalize_key('Café'), normalize_key('Cafe\u0301'))
        self.assertEqual(normalize_key('Canción'), normalize_key('Cancion'))
        self.assertEqual(normalize_key('Ångström'), normalize_key('Angstrom'))
        self.assertNotEqual(normalize_key('ガラス'), normalize_key('カラス'))
        self.assertEqual(normalize_key('ｶﾞﾗｽ'), normalize_key('ガラス'))

    def test_de_pack_word_aliases(self):
        rules = load_language_rules(('de',))
        self.assertEqual(rules.normalize('Fruehling und schöne Blumen'),
                         rules.normalize('Frühling und schoene Blumen'))

    def test_fr_pack_qualifiers(self):
        rules = load_language_rules(('fr',))
        self.assertIn('version francaise', rules.soft_suffixes)
        self.assertIn('en direct', rules.protected_suffixes)

    def test_invalid_locale_tags_or_missing_pack(self):
        for languages in (('../etc',), ('de', 'de'), ('DE',),
                          ('de/../../tmp',), ('a' * 40,), ('zz',)):
            with self.subTest(languages=languages), self.assertRaises(ValueError):
                load_language_rules(languages)

    def test_untrusted_data_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            invalid = [
                'aliases = [["foo", "bar"], ["bar", "baz"]]',
                'aliases = [["two words", "one"]]',
                'aliases = [["a"]]',
                'aliases = [["a", "b"]]\nsoft_suffixes = ["Live"]\nprotected_suffixes = ["Live"]',
                'aliases = []\nscript = "exec evil"',
                'soft_suffixes = "no"',
            ]
            for body in invalid:
                (folder / 'xx.toml').write_text(body, encoding='utf-8')
                with self.subTest(body=body), self.assertRaises(ValueError):
                    load_language_rules(('xx',), rules_dir=folder)

    def test_pack_can_be_added_without_code(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'pt-br.toml').write_text(
                'aliases = [["corassao", "coração"]]\n'
                'soft_suffixes = ["Ao vivo especial"]\n'
                'protected_suffixes = ["Instrumental"]\n', encoding='utf-8')
            rules = load_language_rules(('pt-br',), rules_dir=folder)
            self.assertEqual(rules.normalize('Corassao'), rules.normalize('Coração'))
            self.assertEqual(rules.soft_suffixes, ('ao vivo especial',))


if __name__ == '__main__':
    unittest.main()
