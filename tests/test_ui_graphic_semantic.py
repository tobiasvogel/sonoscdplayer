"""ARC-DISPLAY-01C.4 pixel parity for the opt-in semantic OLED path."""
import unittest
from unittest.mock import patch

from sonos_ui.graphic_views import (
    graphic_frame_from_menu, render_graphic_semantic,
)
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.menu_catalog import NAVIGATION_PAGES
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


if __name__ == "__main__":
    unittest.main()
