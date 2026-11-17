"""ARC-DISPLAY-01C.6: legacy/preview pixel parity for remaining OLED overlays."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from network_setup.session import SetupSnapshot
from sonos_ui.graphic_views import graphic_frame_from_menu, render_graphic_semantic
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.notice import Notice
from sonos_ui.semantic_screen import SemanticScreen, ScreenItem
from sonos_ui.text_input import TextEntry


class SessionStub:
    def __init__(self, snapshot):
        self.current = snapshot

    def snapshot(self):
        return self.current


class RemainingGraphicParityTests(unittest.TestCase):
    def assert_preview_parity(self, menu, *, now=1):
        with patch("sonos_ui.interactive_menu.time.monotonic", return_value=now):
            with patch("sonos_ui.render.MARQUEE_CLOCK.elapsed", return_value=0):
                old = menu.frame(lambda image: image)
                preview = graphic_frame_from_menu(menu, now=now)
        self.assertEqual((old.mode, old.size, old.tobytes()),
                         (preview.mode, preview.size, preview.tobytes()))

    def test_volume_semantic_pixels_at_boundaries_and_middle(self):
        for language in ("de", "en"):
            menu = InteractiveMenu(language=language)
            menu.active, menu.section = True, "main"
            for level in (0, 1, 42, 99, 100):
                with self.subTest(language=language, level=level):
                    menu._volume_level = level
                    menu._volume_until = 20
                    self.assert_preview_parity(menu, now=1)
                    screen = menu.semantic_screen(now=1)
                    self.assertEqual(screen.items[0].value, str(level))
                    image = render_graphic_semantic(
                        screen, section="volume", language=language)
                    self.assertEqual(image.mode, "L")

    def test_volume_invalid_semantic_values_fail_closed(self):
        for value in (None, "", "-1", "101", "2.5", "NaN", "01", "42x"):
            screen = SemanticScreen("idle", items=(
                ScreenItem("volume", "Volume", value=value),))
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    render_graphic_semantic(screen, section="volume")

    def test_notice_pixel_parity_dismissible_and_undismissible(self):
        for language in ("de", "en"):
            for dismissible in (True, False):
                menu = InteractiveMenu(language=language)
                menu.active, menu.section = True, "main"
                menu._notice_previous_active = False
                menu.connect_notices.post(
                    Notice("status.player_offline", severity="error",
                           dismissible=dismissible, duration_seconds=0), now=0)
                with self.subTest(language=language, dismissible=dismissible):
                    self.assert_preview_parity(menu, now=1)
                    semantic = menu.semantic_screen(now=1)
                    self.assertEqual(semantic.kind, "notice")
                    from sonos_ui.semantic_screen import screen_from_notice
                    from sonos_ui.notice_policy import NoticeTiming
                    from sonos_ui.i18n import Translator
                    notice = menu.connect_notices.current(now=1)
                    expected = screen_from_notice(notice, language=language).message
                    if NoticeTiming().encoder_can_confirm(notice):
                        expected += "\n" + Translator(language).tr("notice.dismiss_hint")
                    self.assertEqual(semantic.message, expected)
                    self.assertEqual(menu.section, "main")

    def test_hotspot_and_wifi_choices_pixel_parity(self):
        hotspot = SetupSnapshot(
            state="hotspot", hotspot_ssid="SonosCDPlayer-ABCD",
            password="ABCDEFGH", url="http://192.168.4.1", remaining=640)
        for language in ("de", "en"):
            session = SessionStub(hotspot)
            menu = InteractiveMenu(language=language, network_setup=session)
            for now in (0, 4, 8):
                with self.subTest(language=language, state="hotspot", now=now):
                    self.assertIsNone(menu.semantic_screen(now=now))
                    self.assert_preview_parity(menu, now=now)
            session.current = SetupSnapshot(
                state="choosing", networks=(
                    SimpleNamespace(ssid="Test WiFi", secure=True, signal=85),),
                saved=())
            menu.wifi_entry.mode = "list"
            menu.wifi_entry.focus = 0
            self.assert_preview_parity(menu, now=1)

    def test_wifi_password_entry_is_same_controller_and_same_pixels(self):
        snap = SetupSnapshot(state="choosing", networks=(
            SimpleNamespace(ssid="Test WiFi", secure=True, signal=85),))
        menu = InteractiveMenu(network_setup=SessionStub(snap))
        menu.wifi_entry.mode = "password"
        menu.wifi_entry.target = "Test WiFi"
        menu.wifi_entry.entry = TextEntry(max_bytes=63, text="abcd1234")
        original_entry = menu.wifi_entry.entry
        before = (original_entry.text, original_entry.position)
        self.assert_preview_parity(menu, now=2)
        self.assertIs(original_entry, menu.wifi_entry.entry)
        self.assertEqual((original_entry.text, original_entry.position), before)

    def test_shutdown_remains_explicitly_outside_preview(self):
        menu = InteractiveMenu()
        menu.shutdown_screen = "reboot"
        with self.assertRaises(ValueError):
            graphic_frame_from_menu(menu, now=1)


if __name__ == "__main__":
    unittest.main()
