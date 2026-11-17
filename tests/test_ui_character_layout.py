"""ARC-DISPLAY-01B: offscreen tests, no hardware, Sonos or GPIO."""
import unittest

from sonos_ui.character_layout import portable_text, render_character_screen
from sonos_ui.character_adapters import (
    navigation_screen, render_navigation, text_entry_frame,
    speaker_selection_screen, power_menu_screen, language_menu_screen,
    wifi_choice_screen, network_status_screen,
)
from sonos_ui.display_backend import validate_frame
from sonos_ui.display_capabilities import DisplayCapabilities, SSD1327_CAPABILITIES
from sonos_ui.semantic_screen import ScreenItem, SemanticScreen
from sonos_ui.text_input import TextEntry, DONE, DELETE, CANCEL
from sonos_ui.navigation_pages import MAIN_ITEMS, NETWORK_ITEMS, SETTINGS_ITEMS


LCD16 = DisplayCapabilities(kind="character", columns=16, rows=2, charset="hd44780")
LCD20 = DisplayCapabilities(kind="character", columns=20, rows=4, charset="ascii")


class CharacterLayoutTests(unittest.TestCase):
    def test_exact_frames_and_independent_focus_and_selection(self):
        screen = SemanticScreen("menu", heading="Sprecher", items=(
            ScreenItem("a", "Wohnzimmer", selected=True),
            ScreenItem("b", "Kueche", focused=True),
            ScreenItem("c", "Arbeitszimmer", selected=True),
            ScreenItem("d", "Zurueck")))
        frame = render_character_screen(screen, LCD16)
        self.assertEqual(len(frame.lines), 2)
        self.assertTrue(frame.lines[0].startswith(" *"))
        self.assertTrue(frame.lines[1].startswith("> "))
        validate_frame(LCD16, frame)
        next_screen = SemanticScreen("menu", items=(
            ScreenItem("a", "Wohnzimmer", selected=True),
            ScreenItem("b", "Kueche"),
            ScreenItem("c", "Arbeitszimmer", focused=True, selected=True),
            ScreenItem("d", "Zurueck")))
        frame = render_character_screen(next_screen, LCD16)
        self.assertTrue(frame.lines[1].startswith(">*"))
        self.assertIn("Arbeitszimmer", frame.lines[1])
        self.assertTrue(all(len(line) == 16 for line in frame.lines))

    def test_20x4_heading_and_three_menu_items(self):
        screen = SemanticScreen("menu", heading="Netzwerk", items=(
            ScreenItem(str(i), f"Eintrag{i}", focused=i == 3)
            for i in range(5)))
        frame = render_character_screen(screen, LCD20)
        self.assertEqual(frame.lines[0].strip(), "Netzwerk")
        self.assertIn("Eintrag3", "".join(frame.lines))
        self.assertTrue(all(len(line) == 20 for line in frame.lines))

    def test_notice_wrap_and_pages_without_losing_content(self):
        msg = "Dies ist ein sehr langer Hinweis mit vielen Woertern, der weitergeht"
        screen = SemanticScreen("notice", message=msg, severity="warning")
        first = render_character_screen(screen, LCD16, page=0)
        next_page = render_character_screen(screen, LCD16, page=2)
        self.assertNotEqual(first, next_page)
        self.assertIn("Dies ist ein", first.lines[0])
        self.assertIn("weitergeht", "".join(next_page.lines))
        validate_frame(LCD16, next_page)

    def test_overlong_field_scroll_and_metadata_page(self):
        screen = SemanticScreen("playback", items=(
            ScreenItem("title", "Titel", value="Ein besonders langer Titel"),
            ScreenItem("artist", "Interpret", value="Bach"),
            ScreenItem("track", "Track", value="3/12")))
        a = render_character_screen(screen, LCD16, scroll_offset=0)
        b = render_character_screen(screen, LCD16, scroll_offset=7)
        self.assertNotEqual(a.lines[0], b.lines[0])
        c = render_character_screen(screen, LCD16, page=1)
        self.assertIn("Track", "".join(c.lines))
        self.assertEqual(a.lines[1].strip(), "Interpret: Bach")

    def test_portable_ascii_and_input_validation(self):
        self.assertEqual(portable_text("München ä ß ✓\t"), "Muenchen ae ss * ")
        s = SemanticScreen("notice", message="Ärger", severity="error")
        self.assertEqual(render_character_screen(s, LCD16).lines[0].strip(), "Aerger")
        with self.assertRaises(ValueError):
            render_character_screen(s, SSD1327_CAPABILITIES)
        with self.assertRaises(ValueError):
            render_character_screen(s, LCD16, page=-1)
        with self.assertRaises(ValueError):
            render_character_screen(s, LCD16, scroll_offset=-2)


class CharacterAdapterTests(unittest.TestCase):
    def test_navigation_uses_existing_menu_keys_and_locales(self):
        for section, keys in (("main", MAIN_ITEMS), ("network", NETWORK_ITEMS),
                              ("settings", SETTINGS_ITEMS)):
            for language in ("de", "en"):
                screen = navigation_screen(section, len(keys) - 1, language=language)
                self.assertEqual(tuple(item.key for item in screen.items), keys)
                self.assertTrue(screen.items[-1].focused)
                frame = render_navigation(section, len(keys) - 1, LCD16, language=language)
                self.assertTrue(frame.lines[-1].startswith("> "))
                validate_frame(LCD16, frame)
        with self.assertRaises(ValueError):
            navigation_screen("missing", 0)
        with self.assertRaises(ValueError):
            navigation_screen("main", -1)

    def test_text_entry_two_line_preserves_existing_wheel(self):
        entry = TextEntry(max_bytes=32, text="SSIDae")
        start = text_entry_frame(entry, LCD16, title="SSID")
        self.assertIn("SSIDae|", start.lines[0])
        self.assertTrue(start.lines[1].startswith("> a"))
        entry.turn(-1)
        self.assertEqual(entry.current, DONE)
        self.assertIn("Fertig", text_entry_frame(entry, LCD16, title="SSID").lines[1])
        entry.turn(-1)
        self.assertEqual(entry.current, DELETE)
        entry.turn(-1)
        self.assertEqual(entry.current, CANCEL)
        validate_frame(LCD16, text_entry_frame(entry, LCD16, title="SSID"))

    def test_text_entry_errors_and_20x4_layout_are_read_only(self):
        entry = TextEntry(max_bytes=32, text="falschespasswort")
        entry.error = ("textinput.too_long", {"count": 32})
        before = (entry.text, entry.position, entry.error)
        a = text_entry_frame(entry, LCD16, title="Passwort")
        b = text_entry_frame(entry, LCD20, title="Passwort", subtitle="WLAN")
        self.assertEqual(before, (entry.text, entry.position, entry.error))
        self.assertIn("Passwort", b.lines[0])
        self.assertIn("falschespasswort", b.lines[1])
        self.assertNotIn("falschespasswort", a.lines[0])
        self.assertEqual(len(b.lines), 4)
        for frame, caps in ((a, LCD16), (b, LCD20)):
            validate_frame(caps, frame)

    def test_byte_counts_unicode_and_no_input_side_effect(self):
        entry = TextEntry(extra_characters="ä", text="Mün")
        self.assertEqual(entry.byte_length, 4)
        frame = text_entry_frame(entry, LCD20, title="SSID")
        self.assertIn("Muen", frame.lines[1])
        self.assertIn("4/63", frame.lines[2])
        self.assertIsNone(entry.result)
        self.assertEqual(entry.text, "Mün")


if __name__ == "__main__":
    unittest.main()
