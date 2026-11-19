"""Integrated display priority and read-only boundaries.

Since 01D the SSD1327 frame is drawn from the same semantic screen as the
16x2 character projection; there is no second input controller.
"""
import unittest
from unittest.mock import patch

from network_setup.session import SetupSnapshot
from sonos_ui.character_views import character_frame_from_menu
from sonos_ui.display_capabilities import DisplayCapabilities
from sonos_ui.graphic_views import graphic_frame_from_menu
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.notice import Notice


LCD16 = DisplayCapabilities(
    kind="character", columns=16, rows=2, charset="hd44780")


class SetupStub:
    def __init__(self):
        self.current = SetupSnapshot(
            state="hotspot", hotspot_ssid="SonosCDPlayer-Test",
            password="ABCDEFGH", url="http://192.168.4.1", remaining=600)

    def snapshot(self):
        return self.current


class DisplayIntegrationTests(unittest.TestCase):
    def test_stacked_overlays_follow_controller_precedence(self):
        """Shutdown > notice > WLAN > POWER > volume > language > menu."""
        menu = InteractiveMenu(network_setup=SetupStub(),
                               power_action=lambda _: True)
        menu.active, menu.section = True, "main"
        menu.power_menu = True
        menu._volume_level, menu._volume_until = 42, 100
        menu.language_picker = True
        menu.shutdown_screen = "reboot"
        self.assertEqual(menu.semantic_screen(now=0).kind, "notice")
        self.assertIn("neu", menu.semantic_screen(now=0).message.lower())
        with self.assertRaises(ValueError):
            graphic_frame_from_menu(menu, now=0)

        menu.shutdown_screen = None
        menu._notice_previous_active = False
        menu.connect_notices.post(
            Notice("status.player_offline", severity="error",
                   duration_seconds=0), now=0)
        screen = menu.semantic_screen(now=0)
        self.assertEqual(screen.kind, "notice")
        self.assertEqual(screen.severity, "error")
        with patch("sonos_ui.interactive_menu.time.monotonic", return_value=0):
            with patch("sonos_ui.render.MARQUEE_CLOCK.elapsed", return_value=0):
                original = menu.frame(lambda image: image)
                preview = graphic_frame_from_menu(menu, now=0)
        self.assertEqual(original.tobytes(), preview.tobytes())

        menu.connect_notices.clear(now=0)
        menu._notice_previous_active = None
        menu.power_menu = False
        self.assertIsNone(menu.semantic_screen(now=0))  # WLAN owns its display
        frame = character_frame_from_menu(menu, LCD16, now=0)
        self.assertIn("SSID", frame.lines[0])

        menu.power_menu = True
        screen = menu.semantic_screen(now=0)
        self.assertEqual(tuple(item.key for item in screen.items),
                         ("menu.back", "menu.poweroff", "menu.reboot"))

        menu.power_menu = False
        menu.network_setup.current = SetupSnapshot(state="idle")
        screen = menu.semantic_screen(now=0)
        self.assertEqual(screen.items[0].key, "volume")

        menu._volume_until = 0
        screen = menu.semantic_screen(now=1)
        self.assertEqual(screen.kind, "menu")
        self.assertEqual(tuple(item.key for item in screen.items),
                         tuple(menu.languages))
        self.assertEqual(tuple(item.intent for item in screen.items),
                         tuple("choose_language" for _ in menu.languages))

        menu.language_picker = False
        self.assertEqual(menu.semantic_screen(now=1).items[0].key, "menu.targets")

    def test_preview_renderers_do_not_execute_any_controller_actions(self):
        """Drawing must not call actions, even with actionable menu entries."""
        menu = InteractiveMenu()
        menu.active, menu.section, menu.menu_focus = True, "settings", 1
        before = (menu.active, menu.section, menu.menu_focus, menu.state)
        with patch.object(menu, "_request_scan") as scan, \
             patch.object(menu, "_start_network_setup") as setup, \
             patch.object(menu, "_discover_if_empty") as discover, \
             patch.object(menu, "_navigation_event") as event:
            screen = menu.semantic_screen(now=1)
            self.assertEqual(screen.items[1].key, "menu.rescan")
            graphic_frame_from_menu(menu, now=1)
            character_frame_from_menu(menu, LCD16, now=1)
            scan.assert_not_called()
            setup.assert_not_called()
            discover.assert_not_called()
            event.assert_not_called()
        self.assertEqual(before, (menu.active, menu.section,
                                  menu.menu_focus, menu.state))

    def test_one_input_controller_drives_both_presentations(self):
        menu = InteractiveMenu()
        menu.active, menu.section = True, "main"
        self.assertEqual(menu.menu_focus, 0)
        self.assertTrue(menu._navigation_event("encoder", "right", 0))
        self.assertEqual(menu.menu_focus, 1)
        screen = menu.semantic_screen(now=1)
        self.assertEqual(screen.items[1].key, "menu.network")
        self.assertTrue(screen.items[1].focused)
        graphic_frame_from_menu(menu, now=1)
        character_frame_from_menu(menu, LCD16, now=1)
        self.assertEqual(menu.menu_focus, 1)
        self.assertTrue(menu._navigation_event("encoder_sw", "pressed", 1))
        self.assertEqual(menu.section, "network")
        self.assertEqual(menu.menu_focus, 0)
        self.assertEqual(menu.semantic_screen(now=2).items[0].key,
                         "network.status")

    def test_target_feedback_keeps_legacy_oled_footer_and_semantic_notice(self):
        """Speaker feedback: OLED keeps rows and status line, LCD shows it full-screen."""
        from dataclasses import replace
        from sonos_ui.multi_target import Target, Topology
        target = Target("speaker-opaque", "Wohnzimmer",
                        ("speaker-opaque",), ("Wohnzimmer",))
        menu = InteractiveMenu()
        menu.active, menu.section = True, "targets"
        menu.state = replace(menu.state, topology=Topology((target,)),
                             focus=0, draft=frozenset(("speaker-opaque",)))
        for pending, notice in ((True, "status.target_pending"),
                                (False, "status.target_rejected"),
                                (False, "status.target_accepted"),
                                (False, "status.invalid_selection")):
            with self.subTest(pending=pending, notice=notice):
                menu._target_pending = pending
                menu.state = replace(menu.state, notice=notice)
                semantic = menu.semantic_screen(now=1)
                self.assertEqual((semantic.kind, semantic.view), ("menu", "targets"))
                self.assertTrue(semantic.message)
                with patch("sonos_ui.render.MARQUEE_CLOCK.elapsed", return_value=0):
                    original = menu.frame(lambda image: image)
                    preview = graphic_frame_from_menu(menu, now=1)
                self.assertEqual((original.mode, original.tobytes()),
                                 (preview.mode, preview.tobytes()))
                character = character_frame_from_menu(menu, LCD16, now=1)
                self.assertNotIn("speaker-opaque", "".join(character.lines))
                self.assertNotIn("Wohnzimmer", "".join(character.lines))
                self.assertEqual(menu.section, "targets")
                self.assertEqual(menu.state.notice, notice)

    def test_target_feedback_does_not_override_higher_priority_notice(self):
        """Global notice always wins over the special OLED target footer."""
        from dataclasses import replace
        menu = InteractiveMenu()
        menu.active, menu.section = True, "targets"
        menu.state = replace(menu.state, notice="status.target_rejected")
        menu._target_pending = True
        menu._notice_previous_active = False
        menu.connect_notices.post(
            Notice("status.player_offline", severity="error",
                   duration_seconds=0), now=0)
        self.assertEqual(menu.semantic_screen(now=1).kind, "notice")
        with patch("sonos_ui.interactive_menu.time.monotonic", return_value=1):
            legacy = menu.frame(lambda image: image)
            preview = graphic_frame_from_menu(menu, now=1)
        self.assertEqual(legacy.tobytes(), preview.tobytes())

    def test_oled_draws_whatever_the_semantic_screen_says(self):
        """The OLED never decides precedence itself (single source, 01D)."""
        from dataclasses import replace
        from sonos_ui.notice_render import NoticeRenderer
        from sonos_ui.semantic_screen import screen_from_notice
        menu = InteractiveMenu()
        menu.active, menu.section = True, "targets"
        menu.state = replace(menu.state, notice="status.target_rejected")
        other = screen_from_notice(Notice("status.player_offline", severity="error"))
        with patch.object(menu, "semantic_screen", return_value=other):
            drawn = graphic_frame_from_menu(menu, now=1)
        expected = NoticeRenderer().render_text(other.message, "error", grayscale=True)
        self.assertEqual(drawn.tobytes(), expected.tobytes())

    def test_target_feedback_preview_never_sends_sonos_commands(self):
        """Speaker ACK/rejection presentation must not invoke any controller action."""
        from dataclasses import replace
        menu = InteractiveMenu()
        menu.active, menu.section = True, "targets"
        menu.state = replace(menu.state, notice="status.target_rejected")
        with patch.object(menu, "_send_target") as send, \
             patch.object(menu, "_discover_if_empty") as discover:
            graphic_frame_from_menu(menu, now=1)
            character_frame_from_menu(menu, LCD16, now=1)
            send.assert_not_called()
            discover.assert_not_called()

    def test_character_and_graphic_paths_do_not_add_display_inputs(self):
        """Neither projection confirms a notice or selects a speaker."""
        menu = InteractiveMenu()
        menu.active, menu.section = True, "main"
        menu._notice_previous_active = False
        menu.connect_notices.post(
            Notice("status.player_offline", severity="error",
                   duration_seconds=0), now=0)
        notice_before = menu.connect_notices.current(now=0)
        character_frame_from_menu(menu, LCD16, now=0)
        graphic_frame_from_menu(menu, now=0)
        self.assertIs(menu.connect_notices.current(now=0), notice_before)


if __name__ == "__main__":
    unittest.main()
