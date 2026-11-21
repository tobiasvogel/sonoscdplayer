"""M4b.8: special ST7735S previews, never input actions or hardware."""
import unittest
from unittest.mock import Mock, patch

from sonoscdplayer.network_setup.session import SetupSnapshot
from sonoscdplayer.ui.displays.frame_router import DisplayFrameRouter
from sonoscdplayer.ui.displays.special_screens import (
    startup_or_shutdown_screen, wifi_setup_screen)


class SpecialScreensTests(unittest.TestCase):
    def test_system_messages_are_localized_and_read_only(self):
        for phase in ('start', 'wait', 'reboot', 'safe'):
            with self.subTest(phase=phase):
                screen = startup_or_shutdown_screen(phase, language='de')
                self.assertEqual(screen.kind, 'notice')
                self.assertTrue(screen.message)
        with self.assertRaises(ValueError):
            startup_or_shutdown_screen('poweroff-now')

    def test_hotspot_credentials_are_only_in_screen_data(self):
        snapshot = SetupSnapshot(state='hotspot', hotspot_ssid='Local-ABC',
                                 password='ABCDEFGH', url='http://192.168.4.1')
        screen = wifi_setup_screen(snapshot, language='de')
        self.assertEqual(screen.kind, 'menu')
        self.assertEqual({item.key: item.value for item in screen.items}['password'], 'ABCDEFGH')

    def test_manual_entry_unknown_phase_and_idle_are_not_overwritten(self):
        for snapshot in (SetupSnapshot(state='choosing'), SetupSnapshot(state='scanning'),
                         SetupSnapshot(state='hotspot', manual=True),
                         SetupSnapshot(state='unexpected'), SetupSnapshot()):
            self.assertIsNone(wifi_setup_screen(snapshot))
        self.assertIsNone(wifi_setup_screen(Mock(state='hotspot')))

    def test_progress_states_do_not_execute_setup(self):
        for state in ('preparing', 'connecting', 'connected', 'unavailable'):
            with self.subTest(state=state):
                self.assertEqual(wifi_setup_screen(SetupSnapshot(state=state)).kind, 'notice')

    def test_router_prefers_wlan_snapshot_over_drive_notice(self):
        bridge = Mock()
        bridge.from_screen.side_effect = lambda screen, *, elapsed=0: screen
        menu = Mock(spec=['semantic_screen', 'netsetup_snapshot', 'power_menu'])
        menu.power_menu = False
        menu.netsetup_snapshot.return_value = SetupSnapshot(state='hotspot',
                                                               hotspot_ssid='Test', password='ABCDEFGH')
        menu.semantic_screen.return_value = None
        result = DisplayFrameRouter('st7735s', st_bridge=bridge).frame(
            {'drive_power': {'idle_waking': True}}, menu=menu, now=2)
        self.assertEqual(result.view, 'wifi_setup')
        menu.semantic_screen.assert_called_once_with(now=2)

    def test_router_preserves_manual_owner_and_ssd1327_delegation(self):
        menu = Mock(spec=['semantic_screen', 'netsetup_snapshot', 'power_menu'])
        menu.power_menu = False
        menu.netsetup_snapshot.return_value = SetupSnapshot(state='choosing', manual=True)
        menu.semantic_screen.return_value = None
        bridge = Mock()
        self.assertIsNone(DisplayFrameRouter('st7735s', st_bridge=bridge).frame({}, menu=menu, now=2))
        bridge.from_screen.assert_not_called()
        old = Mock(return_value='oled')
        self.assertEqual(DisplayFrameRouter(oled_frame=old).frame(None), 'oled')

    def test_explicit_system_frame_is_not_automatically_connected_to_service(self):
        bridge = Mock()
        bridge.from_screen.side_effect = lambda screen, *, elapsed=0: screen
        router = DisplayFrameRouter('st7735s', st_bridge=bridge)
        screen = router.system_frame('start', language='en')
        self.assertEqual(screen.kind, 'notice')
        with self.assertRaises(ValueError):
            DisplayFrameRouter(oled_frame=Mock()).system_frame('start')

    def test_real_bridge_renders_system_and_hotspot_rgb565_without_hardware(self):
        with patch('sonoscdplayer.hardware.displays.st7735s.ST7735S.open',
                   side_effect=AssertionError('hardware must stay closed')):
            router = DisplayFrameRouter('st7735s')
            start = router.system_frame('start')
            self.assertEqual((start.width, start.height, start.pixel_format, len(start.data)),
                             (160, 128, 'rgb565', 40960))
            menu = Mock(spec=['semantic_screen', 'netsetup_snapshot', 'power_menu'])
            menu.power_menu = False
            menu.semantic_screen.return_value = None
            menu.netsetup_snapshot.return_value = SetupSnapshot(state='hotspot',
                                                                hotspot_ssid='ABC', password='DEFGH')
            frame = router.frame(None, menu=menu, now=1)
            self.assertEqual(len(frame.data), 40960)


if __name__ == '__main__':
    unittest.main()
