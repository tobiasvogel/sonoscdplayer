"""ARC-DISPLAY-01C.4 pixel parity for the opt-in semantic OLED path."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from sonos_ui.graphic_views import (
    graphic_frame_from_menu, render_graphic_semantic,
)
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.menu_catalog import NAVIGATION_PAGES
from sonos_ui.multi_target import Target, Topology
from sonos_ui.navigation_pages import NavigationRenderer
from sonos_ui.network_status import NetworkStatus
from sonos_ui.semantic_screen import SemanticScreen, ScreenItem


class GraphicSemanticParityTests(unittest.TestCase):
    def assert_same_pixels(self, old, new):
        self.assertEqual(old.mode, "L")
        self.assertEqual(new.mode, "L")
        self.assertEqual(old.size, (128, 128))
        self.assertEqual(new.size, (128, 128))
        self.assertEqual(old.tobytes(), new.tobytes())

    def test_navigation_all_sections_focus_positions_and_languages(self):
        for language in ("de", "en"):
            menu = InteractiveMenu(language=language)
            menu.active = True
            for section, (heading, keys) in NAVIGATION_PAGES.items():
                for focus in range(len(keys)):
                    with self.subTest(language=language, section=section, focus=focus):
                        menu.section, menu.menu_focus = section, focus
                        # Freeze marquee timing for a strict pixel-for-pixel comparison.
                        with patch("sonos_ui.render.MARQUEE_CLOCK.elapsed",
                                   return_value=0):
                            old = menu.frame(lambda image: image)
                            shared = menu.semantic_screen(now=1)
                            new = graphic_frame_from_menu(menu, now=1)
                            standalone = render_graphic_semantic(
                                shared, section=section, language=language)
                        self.assert_same_pixels(old, new)
                        self.assert_same_pixels(old, standalone)
                        self.assertEqual(tuple(item.key for item in shared.items), keys)
                        self.assertEqual([i for i, item in enumerate(shared.items)
                                          if item.focused], [focus])
                        self.assertEqual((menu.section, menu.menu_focus), (section, focus))

    def test_network_status_for_live_value_combinations(self):
        for language in ("de", "en"):
            menu = InteractiveMenu(language=language)
            menu.active, menu.section = True, "network_status"
            for snapshot in (
                NetworkStatus(),
                NetworkStatus(ethernet="connected", wifi="disconnected",
                              preferred="ethernet"),
                NetworkStatus(ethernet="disconnected", wifi="connected",
                              preferred="wifi"),
            ):
                with self.subTest(language=language, snapshot=snapshot):
                    menu.network_snapshot = snapshot
                    with patch("sonos_ui.render.MARQUEE_CLOCK.elapsed",
                               return_value=0):
                        old = menu.frame(lambda image: image)
                        shared = menu.semantic_screen(now=2)
                        new = graphic_frame_from_menu(menu, now=2)
                    self.assert_same_pixels(old, new)
                    self.assertEqual(shared.kind, "idle")
                    self.assertEqual(len(shared.items), 3)

    def test_reject_mismatched_catalog_and_unsupported_screen(self):
        renderer = NavigationRenderer()
        bad = SemanticScreen("menu", heading="Main", items=(
            ScreenItem("unknown", "Unknown", focused=True),))
        with self.assertRaises(ValueError):
            render_graphic_semantic(bad, section="main", renderer=renderer)
        with self.assertRaises(ValueError):
            render_graphic_semantic(bad, section="targets", renderer=renderer)
        with self.assertRaises(TypeError):
            render_graphic_semantic(None, section="main", renderer=renderer)

    def test_unavailable_inactive_or_overlaid_view_does_not_fallback(self):
        menu = InteractiveMenu()
        menu.section = "main"
        with self.assertRaises(ValueError):
            graphic_frame_from_menu(menu, now=0)
        menu.active = True
        menu.section = "targets"
        with self.assertRaises(ValueError):
            graphic_frame_from_menu(menu, now=0)
        menu.section = "main"
        menu.power_menu = True
        with self.assertRaises(ValueError):
            graphic_frame_from_menu(menu, now=0)



class ExtendedGraphicParityTests(GraphicSemanticParityTests):
    @staticmethod
    def pixel_bytes(image):
        return (image.mode, image.size, image.tobytes())

    def assert_preview_matches_legacy(self, menu, *, now=1):
        with patch("sonos_ui.render.MARQUEE_CLOCK.elapsed", return_value=0):
            legacy = menu.frame(lambda image: image)
            semantic = menu.semantic_screen(now=now)
            projected = graphic_frame_from_menu(menu, now=now)
        self.assertEqual(self.pixel_bytes(legacy), self.pixel_bytes(projected))
        return semantic

    def test_power_menu_all_focus_and_notice_states(self):
        for language in ("de", "en"):
            menu = InteractiveMenu(language=language, power_action=lambda action: True)
            menu.active, menu.section, menu.menu_focus = True, "settings", 2
            menu.power_menu = True
            for notice in ("", "status.power_choose"):
                for focus in range(3):
                    with self.subTest(language=language, focus=focus, notice=notice):
                        menu.power_focus = focus
                        menu.power_notice = notice
                        screen = self.assert_preview_matches_legacy(menu)
                        self.assertEqual(screen.items[focus].focused, True)
                        self.assertEqual(tuple(item.key for item in screen.items),
                                         ("menu.back", "menu.poweroff", "menu.reboot"))
                        self.assertEqual(menu.section, "settings")
                        self.assertEqual(menu.menu_focus, 2)

    def test_language_picker_all_focus_and_languages(self):
        for language in ("de", "en"):
            menu = InteractiveMenu(language=language)
            menu.active, menu.section = True, "main"
            menu.language_picker = True
            for focus in range(len(menu.languages)):
                with self.subTest(language=language, focus=focus):
                    menu.language_focus = focus
                    screen = self.assert_preview_matches_legacy(menu)
                    self.assertEqual(screen.items[focus].key, menu.languages[focus])
                    self.assertEqual(menu.language_focus, focus)
                    self.assertTrue(menu.language_picker)

    def test_speaker_checkmarks_focus_empty_and_multiple_targets(self):
        targets = (
            Target("uid-a", "Kueche", ("uid-a",), ("Kueche",)),
            Target("uid-b", "Wohnzimmer", ("uid-b",), ("Wohnzimmer",)),
            Target("uid-c", "Buero", ("uid-c",), ("Buero",)),
            Target("uid-d", "Bad", ("uid-d",), ("Bad",)),
        )
        for language in ("de", "en"):
            menu = InteractiveMenu(language=language)
            menu.active, menu.section = True, "targets"
            for selected in (frozenset(), frozenset(("uid-a", "uid-d"))):
                for focus in range(len(targets) + 2):
                    with self.subTest(language=language, selected=selected, focus=focus):
                        menu.state = replace(menu.state, topology=Topology(targets),
                                             draft=selected, focus=focus, notice="")
                        screen = self.assert_preview_matches_legacy(menu)
                        self.assertEqual(screen.items[0].selected, "uid-a" in selected)
                        self.assertEqual(screen.items[3].selected, "uid-d" in selected)
                        self.assertEqual(screen.items[focus].focused, True)
                        self.assertNotIn("uid-a", tuple(item.label for item in screen.items))
            menu.state = replace(menu.state, topology=Topology(()),
                                 draft=frozenset(), focus=0, notice="")
            screen = self.assert_preview_matches_legacy(menu)
            self.assertEqual(len(screen.items), 2)

    def test_reject_speaker_mismatch_and_missing_state(self):
        menu = InteractiveMenu()
        menu.active, menu.section = True, "targets"
        screen = menu.semantic_screen(now=0)
        with self.assertRaises(ValueError):
            render_graphic_semantic(screen, section="targets")
        mismatched = SemanticScreen("menu", heading=screen.heading, items=(
            ScreenItem("menu.back", "Back", focused=True),
            ScreenItem("menu.review", "Apply")))
        with self.assertRaises(ValueError):
            render_graphic_semantic(mismatched, section="targets", state=menu.state)
        with self.assertRaises(ValueError):
            render_graphic_semantic(screen, section="language")

if __name__ == "__main__":
    unittest.main()
