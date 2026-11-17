"""ARC-DISPLAY-01C: shared navigation catalog, no extra input controller."""
import unittest

from sonos_ui.menu_catalog import (
    MAIN_ITEMS, NETWORK_ITEMS, SETTINGS_ITEMS,
    NAVIGATION_PAGES, navigation_page,
)
from sonos_ui.navigation_pages import (
    MAIN_ITEMS as OLED_MAIN, NETWORK_ITEMS as OLED_NETWORK,
    SETTINGS_ITEMS as OLED_SETTINGS,
)
from sonos_ui.character_adapters import navigation_screen


class SharedMenuCatalogTests(unittest.TestCase):
    def test_existing_oled_exports_keep_same_catalog_objects(self):
        self.assertIs(MAIN_ITEMS, OLED_MAIN)
        self.assertIs(NETWORK_ITEMS, OLED_NETWORK)
        self.assertIs(SETTINGS_ITEMS, OLED_SETTINGS)

    def test_character_items_and_intents_follow_one_catalog(self):
        for section, (heading, keys) in NAVIGATION_PAGES.items():
            self.assertEqual(navigation_page(section), (heading, keys))
            for language in ('de', 'en'):
                for focus in range(len(keys)):
                    screen = navigation_screen(section, focus, language=language)
                    self.assertEqual(tuple(item.key for item in screen.items), keys)
                    self.assertEqual(tuple(item.intent for item in screen.items), keys)
                    self.assertEqual([i for i, item in enumerate(screen.items)
                                      if item.focused], [focus])

    def test_catalog_is_immutable_and_rejects_unknown_pages(self):
        with self.assertRaises(TypeError):
            NAVIGATION_PAGES['main'] = ('wrong', ())
        with self.assertRaises(ValueError):
            navigation_page('targets')
        with self.assertRaises(ValueError):
            navigation_page(None)

    def test_expected_current_menu_order_is_preserved(self):
        self.assertEqual(MAIN_ITEMS, (
            'menu.targets', 'menu.network', 'menu.settings', 'menu.back'))
        self.assertEqual(NETWORK_ITEMS, (
            'network.status', 'network.setup', 'menu.back'))
        self.assertEqual(SETTINGS_ITEMS, (
            'menu.language', 'menu.rescan', 'menu.back'))


if __name__ == '__main__':
    unittest.main()
