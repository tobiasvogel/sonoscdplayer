"""Encoder text entry (D-224)."""
import unittest

from sonos_ui.multi_render import MultiRenderer
from sonos_ui.text_input import (ASCII_CHARACTERS, CANCEL, DELETE, DONE, TextEntry,
                                 clean_extra_characters, wheel_items)
from sonos_ui.text_input_render import render_text_entry, visible_tail
from sonos_ui.ui_config import load_ui_config


def type_text(entry, text):
    for char in text:
        entry.position = entry.items.index(char)
        entry.press()


class WheelTests(unittest.TestCase):
    def test_all_printable_ascii_plus_extras_and_actions(self):
        items = wheel_items('äöüß')
        self.assertTrue(set(ASCII_CHARACTERS) <= set(items))
        self.assertEqual(len(ASCII_CHARACTERS), 95)
        self.assertTrue({'ä', 'ö', 'ü', 'ß'} <= set(items))
        self.assertEqual(items[-3:], (CANCEL, DELETE, DONE))
        self.assertEqual(len(items), len(set(items)))

    def test_extra_characters_are_checked(self):
        self.assertEqual(clean_extra_characters('äaä'), 'ä')
        for bad in ('\n', ' ', 'x' * 0 + '\t'):
            with self.assertRaises(ValueError):
                clean_extra_characters(bad)
        with self.assertRaises(ValueError):
            clean_extra_characters(''.join(chr(0x100 + i) for i in range(65)))

    def test_left_from_start_reaches_done_then_delete_then_cancel(self):
        entry = TextEntry()
        self.assertEqual(entry.current, 'a')
        entry.turn(-1)
        self.assertEqual(entry.current, DONE)
        entry.turn(-1)
        self.assertEqual(entry.current, DELETE)
        entry.turn(-1)
        self.assertEqual(entry.current, CANCEL)

    def test_fast_turning_jumps(self):
        entry = TextEntry()
        entry.turn(1, now=10.0)
        entry.turn(1, now=10.03)
        self.assertEqual(entry.current, 'f')
        entry.turn(1, now=11.0)
        self.assertEqual(entry.current, 'g')
        entry.turn(-1, now=11.02)    # direction change: single step
        self.assertEqual(entry.current, 'f')


class EntryTests(unittest.TestCase):
    def test_type_delete_done(self):
        entry = TextEntry(validate=lambda t: '' if t else ('textinput.empty', {}))
        entry.position = entry.items.index(DONE)
        entry.press()
        self.assertEqual((entry.result, entry.error), (None, ('textinput.empty', {})))
        type_text(entry, 'Ab c!')
        entry.position = entry.items.index(DELETE)
        entry.press()
        self.assertEqual(entry.text, 'Ab c')
        entry.position = entry.items.index(DONE)
        entry.press()
        self.assertEqual(entry.result, 'done')

    def test_byte_limit_counts_utf8(self):
        entry = TextEntry(extra_characters='ä', max_bytes=4)
        type_text(entry, 'äa')
        self.assertEqual(entry.byte_length, 3)
        type_text(entry, 'ä')
        self.assertEqual(entry.text, 'äa')
        self.assertEqual(entry.error, ('textinput.too_long', {'count': 4}))

    def test_cancel_and_reopen_keeps_text(self):
        entry = TextEntry(text='abc')
        entry.position = entry.items.index(CANCEL)
        entry.press()
        self.assertEqual(entry.result, 'cancelled')
        entry.reopen(error=('netsetup.failed', {}))
        self.assertEqual((entry.result, entry.text, entry.current), (None, 'abc', 'a'))


class RenderTests(unittest.TestCase):
    def test_renders_every_wheel_position_in_both_languages(self):
        for language in ('de', 'en'):
            renderer = MultiRenderer(language=language)
            entry = TextEntry(extra_characters='äöüÄÖÜß', text='Pass wort')
            for _ in entry.items:
                image = render_text_entry(renderer, entry, title='T', subtitle='Net',
                                          gray=True)
                self.assertEqual(image.size, (128, 128))
                entry.turn(1)
            entry.error = ('textinput.too_short', {'count': 8})
            render_text_entry(renderer, entry, title='T', gray=True)

    def test_long_text_shows_its_end_with_visible_spaces(self):
        tail, width = visible_tail('x' * 40 + ' end', 106, 11)
        self.assertTrue(tail.endswith('␣end'))
        self.assertLessEqual(width, 106)


class ConfigTests(unittest.TestCase):
    def test_extra_characters_from_ui_toml(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ui.toml'
            self.assertEqual(load_ui_config(path).extra_characters, 'äöüÄÖÜß')
            path.write_text('[ui.text_input]\nextra_characters = "åÅæ"\n', encoding='utf-8')
            self.assertEqual(load_ui_config(path).extra_characters, 'åÅæ')
            path.write_text('[ui.text_input]\nextra_characters = "a b"\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_ui_config(path)


if __name__ == '__main__':
    unittest.main()
