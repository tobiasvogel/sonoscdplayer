"""M4b.10: offline ST7735S sequence and priority regression tests.

No GPIO, SPI, setup events, player commands, or display service are started.
All Wi-Fi credentials are synthetic and must never be printed by this suite.
"""
import unittest
from unittest.mock import Mock, patch

from sonoscdplayer.network_setup.session import SetupSnapshot
from sonoscdplayer.ui.displays.frame_router import DisplayFrameRouter
from sonoscdplayer.ui.menu.text_input import TextEntry
from sonoscdplayer.ui.menu.wifi_entry import WifiEntryController
from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen, ScreenItem


PASSWORD = 'synthetic-passphrase'


def status(mode, *, waking=False, fault=False):
    """Minimal verified schema-v1 player state for offline rendering."""
    count = 0 if mode == 'NO_DISC' else 8
    return {
        'schema_version': 1,
        'player': {'mode': mode, 'track_count': count,
                   'track': None if mode == 'NO_DISC' else 2},
        'drive_power': {'idle_waking': waking, 'idle_wake_fault_latched': fault},
    }


def menu_stub():
    """Only properties queried by the read-only router are present."""
    menu = Mock(spec=['semantic_screen', 'netsetup_snapshot', 'power_menu',
                      'shutdown_screen', 'active', 'language_picker', 'wifi_entry'])
    menu.power_menu = False
    menu.shutdown_screen = None
    menu.active = False
    menu.language_picker = False
    menu.wifi_entry = None
    menu.netsetup_snapshot.return_value = None
    menu.semantic_screen.return_value = None
    return menu


class StateSequenceTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Mock()
        self.bridge.from_screen.side_effect = lambda screen, *, elapsed=0: screen
        self.bridge.from_player_status.side_effect = lambda state, *, elapsed=0: (
            'status', state['player']['mode'])
        self.router = DisplayFrameRouter('st7735s', st_bridge=self.bridge)
        self.menu = menu_stub()

    def test_complete_read_only_state_sequence(self):
        """One router survives all transitions without stale screen ownership."""
        self.assertEqual(self.router.system_frame('start').kind, 'notice')
        for mode in ('NO_DISC', 'STOPPED', 'PLAYING', 'PAUSED'):
            with self.subTest(mode=mode):
                self.assertEqual(self.router.frame(status(mode), menu=self.menu, now=1),
                                 ('status', mode))

        self.menu.active = True
        self.menu.semantic_screen.return_value = SemanticScreen(
            'menu', heading='Speakers', items=(ScreenItem('room', 'Kitchen', focused=True),))
        self.assertEqual(self.router.frame(status('PLAYING'), menu=self.menu, now=2).kind,
                         'menu')

        self.menu.active = False
        self.menu.semantic_screen.return_value = None
        self.menu.netsetup_snapshot.return_value = SetupSnapshot(
            state='hotspot', hotspot_ssid='Test-Network', password=PASSWORD)
        hotspot = self.router.frame(status('STOPPED', waking=True), menu=self.menu, now=3)
        self.assertEqual((hotspot.kind, hotspot.view), ('menu', 'wifi_setup'))

        entry = WifiEntryController(Mock())
        entry.mode, entry.focus = 'list', 0
        self.menu.wifi_entry = entry
        self.menu.netsetup_snapshot.return_value = SetupSnapshot(state='choosing', manual=True)
        chooser = self.router.frame(status('STOPPED', fault=True), menu=self.menu, now=4)
        self.assertEqual((chooser.kind, chooser.view), ('menu', 'wifi_manual'))

        entry.mode = 'password'
        entry.target = 'Test-Network'
        entry.entry = TextEntry(text=PASSWORD)
        password_screen = self.router.frame(status('STOPPED', fault=True), menu=self.menu, now=5)
        self.assertEqual(password_screen.view, 'wifi_manual')
        self.assertNotIn(PASSWORD, str(password_screen))
        self.assertEqual(password_screen.items[0].value, '*' * len(PASSWORD))

        self.menu.netsetup_snapshot.return_value = None
        self.menu.wifi_entry = None
        fault = self.router.frame(status('STOPPED', fault=True), menu=self.menu, now=6)
        self.assertEqual((fault.kind, fault.severity), ('notice', 'error'))
        self.assertEqual(self.router.frame(status('PLAYING'), menu=self.menu, now=7),
                         ('status', 'PLAYING'))
        self.assertEqual(self.router.system_frame('reboot').kind, 'notice')
        self.assertEqual(self.router.system_frame('safe').kind, 'notice')
        entry.session.connect_manual.assert_not_called()
        entry.session.rescan.assert_not_called()
        entry.session.touch.assert_not_called()

    def test_drive_fault_preempts_menu_but_not_shutdown(self):
        self.menu.active = True
        self.menu.semantic_screen.return_value = SemanticScreen('menu', heading='Normal')
        frame = self.router.frame(status('STOPPED', fault=True), menu=self.menu, now=1)
        self.assertEqual(frame.kind, 'notice')
        self.menu.shutdown_screen = 'reboot'
        self.menu.semantic_screen.return_value = SemanticScreen(
            'notice', message='Restart', severity='info')
        frame = self.router.frame(status('STOPPED', fault=True), menu=self.menu, now=2)
        self.assertEqual((frame.kind, frame.message), ('notice', 'Restart'))

    def test_power_menu_preempts_wifi_and_drive_fault(self):
        self.menu.power_menu = True
        self.menu.netsetup_snapshot.return_value = SetupSnapshot(state='hotspot')
        self.menu.semantic_screen.return_value = SemanticScreen('menu', heading='Power')
        self.assertEqual(self.router.frame(status('STOPPED', fault=True),
                                           menu=self.menu, now=1).heading, 'Power')

    def test_unknown_wifi_owner_never_falls_back_to_player(self):
        self.menu.netsetup_snapshot.return_value = object()
        self.assertIsNone(self.router.frame(status('PLAYING'), menu=self.menu, now=1))
        self.bridge.from_player_status.assert_not_called()

    def test_scanning_and_manual_password_keep_wifi_ownership(self):
        self.menu.netsetup_snapshot.return_value = SetupSnapshot(state='scanning', manual=True)
        screen = self.router.frame(status('STOPPED', waking=True), menu=self.menu, now=1)
        self.assertEqual(screen.view, 'wifi_manual')
        self.menu.netsetup_snapshot.return_value = SetupSnapshot(state='choosing', manual=True)
        self.assertIsNone(self.router.frame(status('PLAYING'), menu=self.menu, now=2))
        self.bridge.from_player_status.assert_not_called()

    def test_player_offline_and_invalid_schema_differ(self):
        self.assertEqual(self.router.frame(None).kind, 'notice')
        with self.assertRaisesRegex(ValueError, 'schema'):
            DisplayFrameRouter('st7735s').frame({'schema_version': 2})

    def test_actual_rgb565_contract_across_transitions(self):
        """Real software renderer and converter; no display backend is opened."""
        with patch('sonoscdplayer.hardware.displays.st7735s.ST7735S.open',
                   side_effect=AssertionError('hardware must stay closed')):
            router = DisplayFrameRouter('st7735s')
            frames = [router.system_frame('start'), router.frame(status('NO_DISC')),
                      router.frame(status('PLAYING')), router.frame(status('PAUSED'))]
            self.menu.netsetup_snapshot.return_value = SetupSnapshot(
                state='hotspot', hotspot_ssid='Sample', password='12345678')
            frames.append(router.frame(status('STOPPED'), menu=self.menu, now=1))
            frames.append(router.system_frame('reboot'))
            for index, frame in enumerate(frames):
                with self.subTest(index=index):
                    self.assertEqual((frame.width, frame.height, frame.pixel_format,
                                      len(frame.data)), (160, 128, 'rgb565', 40960))

    def test_ssd1327_path_is_untouched(self):
        old_frame = object()
        old = Mock(return_value=old_frame)
        state = status('PLAYING')
        router = DisplayFrameRouter(oled_frame=old)
        self.assertIs(router.frame(state, menu=self.menu, now=1,
                                   language='en', cover_mode='on'), old_frame)
        old.assert_called_once_with(state, menu=self.menu,
                                    language='en', cover_mode='on')

    def test_no_menu_actions_or_transport_invoked(self):
        self.menu.active = True
        self.menu.semantic_screen.return_value = SemanticScreen('menu', heading='Menu')
        self.router.frame(status('STOPPED'), menu=self.menu, now=1)
        self.menu.semantic_screen.assert_called_once_with(now=1)
        self.bridge.from_player_status.assert_not_called()

    def test_missing_clock_fails_before_menu_inspection(self):
        with self.assertRaisesRegex(ValueError, 'now is required'):
            self.router.frame(status('STOPPED'), menu=self.menu)
        self.menu.semantic_screen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
