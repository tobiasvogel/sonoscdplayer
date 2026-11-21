"""M4b.6: offline frame routing; productive SSD1327 service stays unchanged."""
import unittest
from unittest.mock import Mock, patch

from sonoscdplayer.ui.displays.frame_router import DisplayFrameRouter


class DisplayFrameRouterTests(unittest.TestCase):
    def test_unknown_target_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'not supported'):
            DisplayFrameRouter('sh1107')

    def test_ssd1327_is_default_and_delegates_unchanged_args(self):
        original = object()
        oled = Mock(return_value=original)
        menu = object()
        router = DisplayFrameRouter(oled_frame=oled)
        self.assertIs(router.frame({'ok': True}, menu=menu, now=1.25,
                                   language='en', cover_mode='on'), original)
        oled.assert_called_once_with({'ok': True}, menu=menu, language='en', cover_mode='on')

    def test_missing_menu_clock_is_rejected_before_render(self):
        oled = Mock()
        with self.assertRaisesRegex(ValueError, 'now is required'):
            DisplayFrameRouter(oled_frame=oled).frame({}, menu=object())
        oled.assert_not_called()

    def test_st7735s_renders_semantic_menu_without_actions(self):
        screen, frame = object(), object()
        menu = Mock()
        menu.semantic_screen.return_value = screen
        bridge = Mock()
        bridge.from_screen.return_value = frame
        router = DisplayFrameRouter('st7735s', st_bridge=bridge)
        self.assertIs(router.frame({}, menu=menu, now=9, elapsed=3), frame)
        menu.semantic_screen.assert_called_once_with(now=9)
        bridge.from_screen.assert_called_once_with(screen, elapsed=3)
        bridge.from_player_status.assert_not_called()

    def test_st7735s_returns_none_for_network_setup_ownership(self):
        menu = Mock(spec=['semantic_screen', 'netsetup_snapshot', 'power_menu', 'active'])
        menu.semantic_screen.return_value = None
        menu.netsetup_snapshot.return_value = object()
        menu.power_menu = False
        menu.active = True
        bridge = Mock()
        router = DisplayFrameRouter('st7735s', st_bridge=bridge)
        self.assertIsNone(router.frame({}, menu=menu, now=5))
        bridge.from_screen.assert_not_called()
        bridge.from_player_status.assert_not_called()

    def test_inactive_menu_falls_back_to_verified_player_status(self):
        state, rendered = {'schema_version': 1}, object()
        menu = Mock(spec=['semantic_screen', 'netsetup_snapshot', 'active', 'power_menu'])
        menu.semantic_screen.return_value = None
        menu.netsetup_snapshot.return_value = None
        menu.active, menu.power_menu = False, False
        bridge = Mock()
        bridge.from_player_status.return_value = rendered
        router = DisplayFrameRouter('st7735s', st_bridge=bridge)
        self.assertIs(router.frame(state, menu=menu, now=2), rendered)
        bridge.from_player_status.assert_called_once_with(state, elapsed=0)

    def test_live_hardware_is_not_created(self):
        with patch('sonoscdplayer.hardware.displays.st7735s.ST7735S.open',
                   side_effect=AssertionError('hardware must stay closed')):
            from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen
            router = DisplayFrameRouter('st7735s')
            # Hardware-free native RGB565 frame from an actual semantic screen.
            bridge = router._st_bridge
            self.assertIsNone(bridge)
            fake_menu = Mock()
            fake_menu.semantic_screen.return_value = SemanticScreen('idle')
            frame = router.frame(None, menu=fake_menu, now=1)
            self.assertEqual((frame.width, frame.height, frame.pixel_format, len(frame.data)),
                             (160, 128, 'rgb565', 40960))

    def test_bad_status_not_silently_replaced_by_valid_frame(self):
        router = DisplayFrameRouter('st7735s')
        with self.assertRaisesRegex(ValueError, 'schema'):
            router.frame({'schema_version': 99})


if __name__ == '__main__':
    unittest.main()

class DisplayFramePriorityTests(unittest.TestCase):
    """M4b.7: priority matches the production OLED without hardware actions."""

    def setUp(self):
        self.bridge = Mock()
        self.bridge.from_screen.side_effect = lambda screen, *, elapsed=0: screen
        self.router = DisplayFrameRouter('st7735s', st_bridge=self.bridge)
        self.drive_state = {'drive_power': {'idle_waking': True}}

    def test_drive_notice_preempts_regular_menu_and_volume(self):
        menu = Mock(spec=['semantic_screen', 'netsetup_snapshot', 'active',
                          'power_menu', 'shutdown_screen', 'language_picker'])
        menu.netsetup_snapshot.return_value = None
        menu.power_menu = False
        menu.shutdown_screen = None
        menu.active = True
        result = self.router.frame(self.drive_state, menu=menu, now=10)
        self.assertEqual(result.kind, 'notice')
        menu.semantic_screen.assert_not_called()

    def test_power_overlay_preempts_drive_warning(self):
        from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen
        screen = SemanticScreen('menu', heading='Power')
        menu = Mock(spec=['semantic_screen', 'power_menu', 'shutdown_screen'])
        menu.power_menu, menu.shutdown_screen = True, None
        menu.semantic_screen.return_value = screen
        self.assertIs(self.router.frame(self.drive_state, menu=menu, now=2), screen)
        menu.semantic_screen.assert_called_once_with(now=2)

    def test_shutdown_preempts_drive_warning(self):
        from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen
        screen = SemanticScreen('notice', message='Shutdown', severity='info')
        menu = Mock(spec=['semantic_screen', 'shutdown_screen', 'power_menu'])
        menu.power_menu, menu.shutdown_screen = False, 'reboot'
        menu.semantic_screen.return_value = screen
        self.assertIs(self.router.frame(self.drive_state, menu=menu, now=3), screen)

    def test_wlan_ownership_stays_blank_even_during_drive_fault(self):
        menu = Mock(spec=['semantic_screen', 'netsetup_snapshot', 'power_menu'])
        menu.power_menu = False
        menu.netsetup_snapshot.return_value = Mock(state='hotspot')
        menu.semantic_screen.return_value = None
        self.assertIsNone(self.router.frame({'drive_power': {'idle_wake_fault_latched': True}},
                                             menu=menu, now=4))
        self.bridge.from_screen.assert_not_called()

    def test_drive_notice_variants_and_no_unverified_numbers(self):
        from sonoscdplayer.ui.displays.frame_router import drive_notice_screen
        cases = [
            ({'idle_wake_fault_latched': True, 'idle_wake_retries_exhausted': True},
             'status.drive_wake_exhausted'),
            ({'idle_wake_fault_latched': True, 'idle_wake_retry_in_seconds': None},
             'status.drive_wake_fault'),
            ({'idle_wake_fault_latched': True, 'idle_wake_retry_in_seconds': 5},
             'status.drive_wake_retry'),
            ({'idle_wake_fault_latched': True, 'idle_wake_retry_in_seconds': 0},
             'status.drive_wake_retry_ready'),
            ({'idle_wake_fault_latched': True, 'idle_wake_retry_in_seconds': True},
             'status.drive_wake_fault'),
            ({'idle_waking': True}, 'status.drive_waking'),
        ]
        from sonoscdplayer.ui.notices.notice import Notice
        with patch('sonoscdplayer.ui.screens.semantic_screen.screen_from_notice',
                   side_effect=lambda notice, *, language: notice):
            for power, key in cases:
                with self.subTest(key=key, power=power):
                    notice = drive_notice_screen({'drive_power': power})
                    self.assertIsInstance(notice, Notice)
                    self.assertEqual(notice.key, key)
        self.assertIsNone(drive_notice_screen({'drive_power': {'idle_waking': False}}))

    def test_ssd1327_still_delegates_without_drive_priority_changes(self):
        original = object()
        oled = Mock(return_value=original)
        self.assertIs(DisplayFrameRouter(oled_frame=oled).frame(self.drive_state), original)
        oled.assert_called_once()
