"""Shared read-only semantic source for UI states."""
import unittest
from dataclasses import replace

from sonoscdplayer.network_setup.session import SetupSnapshot
from sonoscdplayer.ui.renderers.character_adapters import (
    navigation_screen, language_menu_screen, power_menu_screen,
    speaker_selection_screen, network_status_screen,
)
from sonoscdplayer.ui.renderers.character_layout import render_character_screen
from sonoscdplayer.ui.renderers.character_views import character_frame_from_menu
from sonoscdplayer.ui.displays.capabilities import DisplayCapabilities
from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
from sonoscdplayer.ui.menu.speakers.multi_target import Target, Topology
from sonoscdplayer.ui.notices.notice import Notice
from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen, ScreenItem


LCD16 = DisplayCapabilities(kind="character", columns=16, rows=2, charset="hd44780")
LCD20 = DisplayCapabilities(kind="character", columns=20, rows=4, charset="ascii")


class SetupStub:
    def __init__(self, snapshot):
        self.value = snapshot

    def snapshot(self):
        return self.value


class SemanticSnapshotTests(unittest.TestCase):
    def test_navigation_pages_are_exactly_shared_and_read_only(self):
        menu = InteractiveMenu()
        menu.active = True
        for section in ("main", "network", "settings"):
            for focus in range(3):
                menu.section, menu.menu_focus = section, focus
                before = (menu.section, menu.menu_focus, menu.active)
                screen = menu.semantic_screen(now=12)
                self.assertEqual(screen, navigation_screen(section, focus))
                self.assertEqual(before, (menu.section, menu.menu_focus, menu.active))
                self.assertEqual(
                    character_frame_from_menu(menu, LCD16, now=12),
                    render_character_screen(screen, LCD16, scroll_offset=24))
                self.assertEqual(
                    character_frame_from_menu(menu, LCD20, now=12),
                    render_character_screen(screen, LCD20, scroll_offset=24))

    def test_power_language_network_and_target_projections(self):
        menu = InteractiveMenu(power_action=lambda action: True)
        menu.active = True
        menu.section = "network_status"
        self.assertEqual(menu.semantic_screen(now=0),
                         network_status_screen(menu.network_snapshot))

        menu.power_menu = True
        menu.power_focus = 2
        self.assertEqual(menu.semantic_screen(now=0), power_menu_screen(2))
        menu.power_menu = False

        menu.language_picker = True
        menu.language_focus = 0
        self.assertEqual(menu.semantic_screen(now=0),
                         language_menu_screen(tuple(menu.languages), 0))
        menu.language_picker = False

        target = Target("opaque-uid", "Zimmer", ("opaque-uid",), ("Zimmer",))
        menu.section = "targets"
        menu.state = replace(menu.state,
                             topology=Topology((target,)),
                             draft=frozenset(("opaque-uid",)),
                             focus=0)
        self.assertEqual(menu.semantic_screen(now=0),
                         speaker_selection_screen(menu.state))
        self.assertNotIn("opaque-uid",
                         "".join(character_frame_from_menu(menu, LCD16, now=0).lines))

    def test_notice_and_shutdown_precedence_and_no_state_mutation(self):
        menu = InteractiveMenu()
        menu.active = True
        menu.section = "main"
        menu._notice_previous_active = False
        menu.connect_notices.post(
            Notice("status.player_offline", severity="error", duration_seconds=0), now=0)
        notice = menu.semantic_screen(now=0)
        self.assertEqual(notice.kind, "notice")
        self.assertIn("Player", notice.message)
        self.assertNotIn("Schliessen", notice.message)
        self.assertIn("Schliessen", notice.footer)   # how to close it
        self.assertEqual(menu.section, "main")
        self.assertEqual(character_frame_from_menu(menu, LCD16, now=3),
                         render_character_screen(menu.semantic_screen(now=3), LCD16,
                                                 page=1, scroll_offset=6))
        menu.shutdown_screen = "reboot"
        self.assertIn("neu", menu.semantic_screen(now=4).message.lower())

    def test_wlan_setup_and_inactive_state_are_intentionally_delegated(self):
        snap = SetupSnapshot(state="hotspot",
                             hotspot_ssid="SonosCDPlayer-ABCD", password="ABCDEFGH",
                             url="http://192.168.4.1", remaining=600)
        menu = InteractiveMenu(network_setup=SetupStub(snap))
        self.assertIsNone(menu.semantic_screen(now=0))
        self.assertIn("SSID", character_frame_from_menu(menu, LCD16, now=0).lines[0])
        menu.network_setup.value = SetupSnapshot(state="idle")
        self.assertIsNone(menu.semantic_screen(now=0))
        base = SemanticScreen("idle", items=(ScreenItem("status", "Status", value="Stop"),))
        self.assertIn("Stop",
                      "".join(character_frame_from_menu(menu, LCD16, now=0,
                                                         base_screen=base).lines))

    def test_volume_overlay_then_normal_navigation(self):
        menu = InteractiveMenu()
        menu.active = True
        menu.section = "main"
        menu._volume_level = 42
        menu._volume_until = 10
        overlay = menu.semantic_screen(now=0)
        self.assertEqual(overlay.kind, "idle")
        self.assertIn("42", "".join(character_frame_from_menu(menu, LCD16, now=0).lines))
        self.assertEqual(menu.semantic_screen(now=11).kind, "menu")


if __name__ == "__main__":
    unittest.main()
