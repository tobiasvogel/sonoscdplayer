"""ARC-DISPLAY-01C: shared navigation catalog, no extra input controller."""
import unittest
from unittest.mock import patch

from sonos_ui.menu_catalog import (
    MAIN_ITEMS, NETWORK_ITEMS, SETTINGS_ITEMS,
    NAVIGATION_PAGES, navigation_page,
)
from sonos_ui.navigation_pages import (
    MAIN_ITEMS as OLED_MAIN, NETWORK_ITEMS as OLED_NETWORK,
    SETTINGS_ITEMS as OLED_SETTINGS,
)
from sonos_ui.character_adapters import navigation_screen
from sonos_ui.interactive_menu import InteractiveMenu


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



class NavigationIntentTests(unittest.TestCase):
    """Reordering a menu must not redirect any physical input action."""

    @staticmethod
    def menu(section, focus, *, active=True):
        menu = InteractiveMenu()
        menu.section = section
        menu.menu_focus = focus
        menu.active = active
        return menu

    def test_main_actions_follow_keys_after_reordering(self):
        reordered = dict(NAVIGATION_PAGES)
        reordered['main'] = ('menu.main', (
            'menu.back', 'menu.settings', 'menu.network', 'menu.targets'))
        with patch('sonos_ui.interactive_menu.NAVIGATION_PAGES', reordered):
            menu = self.menu('main', 2)
            self.assertTrue(menu._navigation_event('encoder_sw', 'pressed', 0))
            self.assertEqual((menu.section, menu.menu_focus), ('network', 0))

            menu = self.menu('main', 1)
            self.assertTrue(menu._navigation_event('encoder_sw', 'pressed', 0))
            self.assertEqual((menu.section, menu.menu_focus), ('settings', 0))

            menu = self.menu('main', 0)
            self.assertTrue(menu._navigation_event('encoder_sw', 'pressed', 0))
            self.assertFalse(menu.active)

            menu = self.menu('main', 3)
            with patch.object(menu, '_discover_if_empty') as discover:
                self.assertTrue(menu._navigation_event('encoder_sw', 'pressed', 0))
            self.assertEqual(menu.section, 'targets')
            self.assertEqual(menu._targets_return, 'main')
            discover.assert_called_once_with(0)

    def test_submenu_actions_and_return_focus_follow_keys(self):
        reordered = dict(NAVIGATION_PAGES)
        reordered['main'] = ('menu.main', (
            'menu.back', 'menu.settings', 'menu.network', 'menu.targets'))
        reordered['network'] = ('menu.network', (
            'menu.back', 'network.setup', 'network.status'))
        reordered['settings'] = ('menu.settings', (
            'menu.back', 'menu.rescan', 'menu.language'))

        with patch('sonos_ui.interactive_menu.NAVIGATION_PAGES', reordered):
            net = self.menu('network', 2)
            with patch.object(net, '_open_network_status') as status:
                self.assertTrue(net._navigation_event('encoder_sw', 'pressed', 3))
                status.assert_called_once_with(3)

            net = self.menu('network', 0)
            self.assertTrue(net._navigation_event('encoder_sw', 'pressed', 4))
            self.assertEqual((net.section, net.menu_focus), ('main', 2))

            settings = self.menu('settings', 1)
            with patch.object(settings, '_request_scan') as scan:
                self.assertTrue(settings._navigation_event('encoder_sw', 'pressed', 5))
                scan.assert_called_once_with(5)

            settings = self.menu('settings', 2)
            self.assertTrue(settings._navigation_event('encoder_sw', 'pressed', 6))
            self.assertTrue(settings.language_picker)

            settings = self.menu('settings', 0)
            self.assertTrue(settings._navigation_event('encoder_sw', 'pressed', 7))
            self.assertEqual((settings.section, settings.menu_focus), ('main', 1))

    def test_unknown_action_or_invalid_focus_fails_closed(self):
        reordered = dict(NAVIGATION_PAGES)
        reordered['main'] = ('menu.main', ('unknown.action',))
        with patch('sonos_ui.interactive_menu.NAVIGATION_PAGES', reordered):
            menu = self.menu('main', 0)
            self.assertFalse(menu._navigation_event('encoder_sw', 'pressed', 0))
            self.assertEqual((menu.section, menu.menu_focus, menu.active),
                             ('main', 0, True))
            menu.menu_focus = 1
            self.assertFalse(menu._navigation_event('encoder_sw', 'pressed', 0))
            self.assertEqual(menu.section, 'main')

    def test_existing_navigation_order_still_works(self):
        menu = self.menu('main', 0)
        self.assertTrue(menu._navigation_event('encoder', 'right', 0))
        self.assertEqual(menu.menu_focus, 1)
        self.assertTrue(menu._navigation_event('encoder_sw', 'pressed', 0))
        self.assertEqual(menu.section, 'network')


if __name__ == '__main__':
    unittest.main()
