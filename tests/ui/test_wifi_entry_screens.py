"""M4b.9 offline projection of the existing manual WLAN controller."""
import unittest
from unittest.mock import Mock, patch

from sonoscdplayer.network_setup.session import SetupSnapshot
from sonoscdplayer.ui.displays.wifi_entry_screens import wifi_entry_screen
from sonoscdplayer.ui.displays.frame_router import DisplayFrameRouter
from sonoscdplayer.ui.menu.wifi_entry import WifiEntryController
from sonoscdplayer.ui.menu.text_input import TextEntry


class WifiEntryScreenTests(unittest.TestCase):
    def test_list_focus_and_actions_are_only_labels(self):
        network = Mock(ssid='Kitchen', secure=True)
        snap = SetupSnapshot(state='choosing', manual=True, networks=(network,))
        ctrl = WifiEntryController(Mock())
        ctrl.mode, ctrl.focus = 'list', 2
        screen = wifi_entry_screen(ctrl, snap)
        self.assertEqual(screen.kind, 'menu')
        self.assertEqual(len(screen.items), 4)
        self.assertEqual([x.focused for x in screen.items], [False, False, True, False])
        ctrl.session.connect_manual.assert_not_called()
        ctrl.session.rescan.assert_not_called()

    def test_password_characters_are_masked(self):
        ctrl = WifiEntryController(Mock())
        ctrl.mode, ctrl.target = 'password', 'Kitchen'
        ctrl.entry = TextEntry(text='do-not-show')
        screen = wifi_entry_screen(ctrl, SetupSnapshot(state='choosing', manual=True))
        self.assertEqual(screen.items[0].value, '*' * len(ctrl.entry.text))
        self.assertNotIn(ctrl.entry.text, str(screen))
        self.assertIn('*' * len(ctrl.entry.text), screen.items[0].label)
        self.assertEqual(screen.items[1].label, ctrl.entry.current)

    def test_ssid_text_remains_visible_and_no_network_action(self):
        ctrl = WifiEntryController(Mock())
        ctrl.mode = 'ssid'
        ctrl.entry = TextEntry(text='Guest Wi-Fi')
        self.assertEqual(wifi_entry_screen(ctrl, SetupSnapshot(state='choosing')).items[0].value,
                         'Guest Wi-Fi')
        ctrl.session.connect_manual.assert_not_called()

    def test_scanning_shows_notice_without_controller(self):
        screen = wifi_entry_screen(None, SetupSnapshot(state='scanning', manual=True))
        self.assertEqual((screen.kind, screen.view), ('notice', 'wifi_manual'))

    def test_invalid_and_inactive_state_remain_unowned(self):
        ctrl = WifiEntryController(Mock())
        self.assertIsNone(wifi_entry_screen(ctrl, SetupSnapshot(state='idle')))
        self.assertIsNone(wifi_entry_screen(ctrl, SetupSnapshot(state='choosing')))
        ctrl.mode, ctrl.focus = 'list', 900
        self.assertIsNone(wifi_entry_screen(ctrl, SetupSnapshot(state='choosing')))

    def test_router_uses_existing_controller_without_input_actions(self):
        ctrl = WifiEntryController(Mock())
        ctrl.mode = 'password'
        ctrl.entry = TextEntry(text='SECRETTEST')
        ctrl.target = 'Network'
        menu = Mock(spec=['semantic_screen', 'netsetup_snapshot', 'power_menu', 'wifi_entry'])
        menu.power_menu = False
        menu.wifi_entry = ctrl
        menu.semantic_screen.return_value = None
        menu.netsetup_snapshot.return_value = SetupSnapshot(state='choosing', manual=True)
        bridge = Mock()
        bridge.from_screen.side_effect = lambda screen, *, elapsed: screen
        result = DisplayFrameRouter('st7735s', st_bridge=bridge).frame(None, menu=menu, now=2)
        self.assertEqual(result.view, 'wifi_manual')
        self.assertNotIn('SECRETTEST', str(result))
        ctrl.session.connect_manual.assert_not_called()

    def test_ssd1327_path_remains_delegated(self):
        old = Mock(return_value='oled-frame')
        self.assertEqual(DisplayFrameRouter(oled_frame=old).frame(None), 'oled-frame')
        old.assert_called_once()


if __name__ == '__main__':
    unittest.main()
