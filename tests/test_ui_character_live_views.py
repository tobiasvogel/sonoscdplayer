"""ARC-DISPLAY-01B: read-only full-menu projection on tiny displays."""
import unittest
from types import SimpleNamespace

from network_setup.session import SetupSnapshot
from sonos_ui.character_views import character_frame_from_menu
from sonos_ui.character_layout import character_page_count, render_character_screen
from sonos_ui.display_backend import validate_frame
from sonos_ui.display_capabilities import DisplayCapabilities
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.multi_target import MultiSelection, Target, Topology
from sonos_ui.notice import Notice
from sonos_ui.semantic_screen import SemanticScreen, ScreenItem
from sonos_ui.text_input import TextEntry


LCD16 = DisplayCapabilities(kind="character", columns=16, rows=2, charset="hd44780")
LCD20 = DisplayCapabilities(kind="character", columns=20, rows=4, charset="ascii")


class SnapshotSession:
    def __init__(self, snapshot):
        self.current = snapshot

    def snapshot(self):
        return self.current


class CharacterLiveViewTests(unittest.TestCase):
    def render(self, menu, now=0, caps=LCD16, base=None):
        frame = character_frame_from_menu(menu, caps, now=now, base_screen=base)
        validate_frame(caps, frame)
        self.assertEqual(len(frame.lines), caps.rows)
        self.assertTrue(all(len(line) == caps.columns for line in frame.lines))
        return frame

    def test_inactive_requires_actual_view_model_not_placeholder(self):
        menu = InteractiveMenu()
        with self.assertRaises(ValueError):
            self.render(menu)
        screen = SemanticScreen("idle", items=(ScreenItem("playback", "Status", value="Stop"),))
        self.assertIn("Stop", "".join(self.render(menu, base=screen).lines))

    def test_navigation_focus_power_language_and_speaker_draft(self):
        menu = InteractiveMenu(power_action=lambda _: True)
        menu.active = True
        for section in ("main", "network", "settings"):
            menu.section = section
            menu.menu_focus = 1
            self.assertIn(">", "".join(self.render(menu).lines))
        menu.power_menu = True
        menu.power_focus = 2
        self.assertIn("Neustarten", "".join(self.render(menu).lines))
        menu.power_menu = False
        menu.language_picker = True
        menu.language_focus = 0
        self.assertIn(">", "".join(self.render(menu).lines))
        menu.language_picker = False

        target = Target("uid-internal", "Wohnzimmer", ("uid-internal",), ("Wohnzimmer",))
        menu.section = "targets"
        menu.state = MultiSelection(topology=Topology((target,)),
                                    draft=frozenset(("uid-internal",)), focus=0)
        self.assertIn(">*", "".join(self.render(menu).lines))
        # Only labels are written to the output, never target UIDs.
        self.assertNotIn("uid-internal", "".join(self.render(menu).lines))

    def test_notices_have_priority_and_read_only_auto_pagination(self):
        menu = InteractiveMenu()
        menu.active = True
        menu.section = "main"
        menu._notice_previous_active = False
        menu.connect_notices.post(Notice("status.player_offline", severity="error",
                                        duration_seconds=0), now=0)
        first = self.render(menu, now=0)
        second = self.render(menu, now=6)
        self.assertIn("Player", "".join(first.lines))
        self.assertIn("Schliessen", "".join(second.lines))
        self.assertEqual(menu.section, "main")
        self.assertTrue(menu.connect_notices.current(now=4) is not None)

    def test_16x2_hotspot_shows_credentials_and_cancel(self):
        snap = SetupSnapshot(state="hotspot", hotspot_ssid="SonosCDPlayer-ABCD",
                             password="ABCDEFGH", url="http://192.168.4.1",
                             remaining=700)
        menu = InteractiveMenu(network_setup=SnapshotSession(snap))
        at0 = self.render(menu, now=0)
        at3 = self.render(menu, now=3)
        at6 = self.render(menu, now=6)
        at12 = self.render(menu, now=12)
        self.assertIn("SSID", at0.lines[0])
        self.assertIn("ABCDEFGH", "".join(at3.lines))
        self.assertIn("URL", at6.lines[0])
        self.assertIn("CONNECT", "".join(at12.lines))
        self.assertEqual(snap.password, "ABCDEFGH")

    def test_wifi_selection_and_failing_password_retain_text(self):
        snap = SetupSnapshot(state="choosing", networks=(
            SimpleNamespace(ssid="WLAN A", secure=True, signal=80),),
            saved=())
        session = SnapshotSession(snap)
        menu = InteractiveMenu(network_setup=session)
        menu.wifi_entry.mode = "list"
        menu.wifi_entry.focus = 0
        self.assertIn("WLAN A", "".join(self.render(menu).lines))
        menu.wifi_entry.mode = "password"
        menu.wifi_entry.target = "WLAN A"
        entry = TextEntry(max_bytes=63, text="abcdefgh")
        entry.error = ("netsetup.failed", {})
        menu.wifi_entry.entry = entry
        before = (entry.text, entry.error, entry.position)
        self.assertIn("Verbindung", "".join(self.render(menu, now=0).lines))
        self.assertIn("abcdefgh", "".join(self.render(menu, now=3).lines))
        self.assertIn("WLAN A", "".join(self.render(menu, now=6).lines))
        self.assertEqual((entry.text, entry.error, entry.position), before)
        frame20 = self.render(menu, now=0, caps=LCD20)
        self.assertIn("abcdefgh", "".join(frame20.lines))

    def test_status_and_shutdown_and_volume_priority(self):
        menu = InteractiveMenu()
        menu.active = True
        menu.section = "network_status"
        self.assertIn("Ethernet", "".join(self.render(menu, now=0).lines))
        menu._volume_level = 42
        menu._volume_until = 100
        self.assertIn("42", "".join(self.render(menu, now=0).lines))
        menu.shutdown_screen = "reboot"
        self.assertIn("neu", "".join(self.render(menu, now=0).lines).lower())

    def test_long_text_is_accessible_without_encoder_pagination(self):
        screen = SemanticScreen("notice", message=(
            "Sehr langer Text fuer ein kleines Zeichendisplay, "
            "mit einem wirklich wichtigen Hinweis am Ende."),
            severity="warning")
        count = character_page_count(screen, LCD16)
        self.assertGreater(count, 2)
        frames = [render_character_screen(screen, LCD16, page=i) for i in range(count)]
        self.assertIn("Ende.", "".join("".join(f.lines) for f in frames))
        self.assertEqual(character_page_count(screen, LCD20) >= 1, True)


if __name__ == "__main__":
    unittest.main()
