"""NET-02: on-device WLAN choice from the hotspot screen (D-224)."""
import time
import unittest

from network_setup.nm import NEW_PRIORITY, OLDER_PRIORITY, Nm, NmError, WifiNetwork
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.text_input import DONE

from test_network_setup import FakeNm, FakeRunner, make_session

HOME = WifiNetwork('Home', 80, True)
CAFE = WifiNetwork('Cafe', 60, False)


class NmSavedTests(unittest.TestCase):
    def runner(self, up_code=0):
        return FakeRunner({
            'NAME,TYPE connection show': (0, 'Imager\\:wifi:802-11-wireless\n'
                                             'sonoscdplayer-wifi-1:802-11-wireless\n'
                                             'sonoscdplayer-setup:802-11-wireless\n'
                                             'Wired:802-3-ethernet\n'),
            'connection show id Imager:wifi': (0, 'Home \n'),
            'connection show id sonoscdplayer-wifi-1': (0, 'Office\n'),
            'connection up': (up_code, ''),
            'NAME connection show': (0, 'sonoscdplayer-wifi-1\nsonoscdplayer-wifi-2\n'),
        })

    def test_saved_profiles_maps_ssid_without_hotspot(self):
        profiles = Nm(runner=self.runner()).saved_profiles()
        self.assertEqual(profiles, {'Home ': 'Imager:wifi', 'Office': 'sonoscdplayer-wifi-1'})

    def test_connect_saved_activates_without_password(self):
        runner = self.runner()
        Nm(runner=runner).connect_saved('wlan0', 'Office')
        calls = runner.joined()
        self.assertTrue(any('connection up id sonoscdplayer-wifi-1 ifname wlan0' in c
                            for c in calls))
        self.assertTrue(any(f'modify id sonoscdplayer-wifi-1 connection.autoconnect-priority '
                            f'{NEW_PRIORITY}' in c for c in calls))
        self.assertTrue(any(f'modify id sonoscdplayer-wifi-2 connection.autoconnect-priority '
                            f'{OLDER_PRIORITY}' in c for c in calls))

    def test_foreign_profile_is_only_activated(self):
        runner = self.runner()
        Nm(runner=runner).connect_saved('wlan0', 'Home ')
        self.assertFalse(any('modify' in c for c in runner.joined()))

    def test_connect_saved_failure_and_unknown(self):
        with self.assertRaises(NmError):
            Nm(runner=self.runner(up_code=4)).connect_saved('wlan0', 'Office')
        with self.assertRaises(NmError):
            Nm(runner=self.runner()).connect_saved('wlan0', 'Unknown')

    def test_hidden_network_profile(self):
        runner = FakeRunner()
        Nm(runner=runner).connect('wlan0', 'Secret', 'secret123', hidden=True)
        add = next(c for c in runner.joined() if 'connection add' in c)
        self.assertIn('802-11-wireless.hidden yes', add)


class ManualNm(FakeNm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.networks = [HOME, CAFE]

    def scan(self, device):
        return list(self.networks)


def hotspot_session(nm):
    session, clock, webs = make_session(nm)
    session.start()
    session.join()
    return session, clock, webs


class SessionManualTests(unittest.TestCase):
    def test_manual_ends_hotspot_and_lists_networks(self):
        nm = ManualNm()
        nm.saved = {'Home': 'p'}
        session, _clock, webs = hotspot_session(nm)
        self.assertTrue(session.manual())
        session.join()
        snap = session.snapshot()
        self.assertEqual((snap.state, snap.manual, snap.saved), ('choosing', True, ('Home',)))
        self.assertEqual(snap.networks, (HOME, CAFE))
        self.assertTrue(webs[0].stopped)
        self.assertIn(('stop',), nm.log)

    def test_empty_rescan_keeps_previous_list(self):
        nm = ManualNm()
        session, _clock, _webs = hotspot_session(nm)
        nm.networks = []
        session.manual()
        session.join()
        self.assertEqual(session.snapshot().networks, (HOME, CAFE))

    def test_back_to_hotspot_same_identity(self):
        nm = ManualNm()
        session, _clock, _webs = hotspot_session(nm)
        session.manual()
        session.join()
        self.assertTrue(session.back_to_hotspot())
        session.join()
        snap = session.snapshot()
        self.assertEqual((snap.state, snap.hotspot_ssid, snap.password),
                         ('hotspot', 'SonosCDPlayer-ABCD', 'K7M3Q9TX'))

    def test_saved_then_password_connects(self):
        nm = ManualNm()
        nm.saved_ok = False
        session, clock, _webs = hotspot_session(nm)
        session.manual()
        session.join()
        self.assertEqual(session.connect_manual('Home', None), '')
        session.join()
        snap = session.snapshot()
        self.assertEqual((snap.state, snap.error, snap.target), ('choosing', 'connect_failed', 'Home'))
        self.assertEqual(session.connect_manual('Home', 'secret123'), '')
        session.join()
        self.assertEqual(session.snapshot().state, 'connected')
        self.assertEqual(nm.log[-2:], [('saved', 'Home'), ('connect', 'Home', 'secret123')])

    def test_choosing_times_out_and_input_extends(self):
        session, clock, _webs = hotspot_session(ManualNm())
        session.manual()
        session.join()
        clock.now += 800
        session.touch()
        clock.now += 800
        session.tick()
        self.assertEqual(session.snapshot().state, 'choosing')
        clock.now += 101
        session.tick()
        session.join()
        self.assertEqual(session.snapshot().state, 'idle')

    def test_web_submit_marks_unlisted_network_hidden(self):
        nm = ManualNm()
        session, _clock, _webs = hotspot_session(nm)
        session.submit('Secret', 'secret123')
        session.join()
        self.assertIn(('connect', 'Secret', 'secret123', 'hidden'), nm.log)


class MenuManualTests(unittest.TestCase):
    """Drive the real menu with a real session on a fake NetworkManager."""

    def setUp(self):
        self.nm = ManualNm()
        self.session, self.clock, _webs = make_session(self.nm)
        self.menu = InteractiveMenu(discover=lambda **_: None, network_setup=self.session)
        self.menu.event('connect', 'setup_requested', now=time.monotonic())
        self.settle()

    def settle(self):
        self.session.join()
        self.menu.poll(now=time.monotonic())

    def key(self, name, event='pressed'):
        result = self.menu.event(name, event, now=time.monotonic())
        self.settle()
        return result

    def frame(self):
        return self.menu.frame(lambda image: image)

    def type_and_finish(self, text):
        entry = self.menu.wifi_entry.entry
        for char in text:
            entry.position = entry.items.index(char)
            self.key('encoder_sw')
        entry.position = entry.items.index(DONE)
        self.key('encoder_sw')

    def test_hotspot_press_opens_list(self):
        self.assertEqual(self.session.snapshot().state, 'hotspot')
        self.frame()
        self.assertTrue(self.key('encoder_sw'))
        self.assertEqual(self.menu.wifi_entry.mode, 'list')
        self.frame()
        self.key('encoder', 'right')
        self.key('encoder', 'right')
        self.key('encoder', 'right')
        self.key('encoder', 'right')   # clamps at "Back to hotspot"
        self.assertEqual(self.menu.wifi_entry.focus, 3)
        self.frame()
        self.key('encoder_sw')
        self.assertEqual(self.session.snapshot().state, 'hotspot')

    def test_saved_network_connects_without_password(self):
        self.nm.saved = {'Home': 'p'}
        self.key('encoder_sw')
        self.key('encoder_sw')         # Home
        self.assertEqual(self.nm.log[-1], ('saved', 'Home'))
        self.assertEqual(self.session.snapshot().state, 'connected')

    def test_saved_failure_asks_for_password_then_connects(self):
        self.nm.saved = {'Home': 'p'}
        self.nm.saved_ok = False
        self.key('encoder_sw')
        self.key('encoder_sw')
        wifi = self.menu.wifi_entry
        self.assertEqual((wifi.mode, wifi.entry.error), ('password', ('netsetup.saved_failed', {})))
        self.frame()
        self.type_and_finish('secret123')
        self.assertEqual(self.nm.log[-1], ('connect', 'Home', 'secret123'))

    def test_wrong_password_reopens_entry_with_text(self):
        self.nm.connect_ok = False
        self.key('encoder_sw')
        self.key('encoder_sw')
        self.type_and_finish('short')          # too short: stays in entry
        self.assertEqual(self.menu.wifi_entry.entry.error, ('textinput.too_short', {'count': 8}))
        self.type_and_finish('123')
        wifi = self.menu.wifi_entry
        self.assertEqual((wifi.mode, wifi.entry.text, wifi.entry.error),
                         ('password', 'short123', ('netsetup.failed', {})))

    def test_open_network_connects_directly(self):
        self.key('encoder_sw')
        self.key('encoder', 'right')
        self.key('encoder_sw')               # Cafe (open)
        self.assertEqual(self.nm.log[-1], ('connect', 'Cafe', ''))

    def test_hidden_network_name_with_umlaut_then_open(self):
        self.key('encoder_sw')
        for _ in range(2):
            self.key('encoder', 'right')
        self.key('encoder_sw')               # hidden network
        self.assertEqual(self.menu.wifi_entry.mode, 'ssid')
        self.frame()
        self.type_and_finish('Küche')
        self.assertEqual(self.menu.wifi_entry.mode, 'password')
        self.type_and_finish('')             # open hidden network
        self.assertEqual(self.nm.log[-1], ('connect', 'Küche', '', 'hidden'))

    def test_password_entry_offers_ascii_only(self):
        self.key('encoder_sw')
        self.key('encoder_sw')
        self.assertNotIn('ä', self.menu.wifi_entry.entry.items)

    def test_cancel_in_entry_returns_to_list_and_connect_cancels_setup(self):
        self.key('encoder_sw')
        self.key('encoder_sw')
        entry = self.menu.wifi_entry.entry
        entry.position = len(entry.items) - 3   # CANCEL
        self.key('encoder_sw')
        self.assertEqual(self.menu.wifi_entry.mode, 'list')
        self.menu.event('connect', 'setup_requested', now=time.monotonic())
        self.settle()
        self.assertIsNone(self.menu.netsetup_snapshot())
        self.assertIsNone(self.menu.wifi_entry.mode)


class CancelByConnectTests(unittest.TestCase):
    """CONNECT held again during a running setup (bug report 17.11.)."""

    def setUp(self):
        self.session, _clock, _webs = make_session(ManualNm())
        self.menu = InteractiveMenu(discover=lambda **_: None, network_setup=self.session)

    def hold(self, actions):
        for action in actions:
            self.menu.event('connect', action, now=time.monotonic())
            self.session.join()
            self.menu.poll(now=time.monotonic())

    def start_from_idle(self):
        # A normal start: hint at 3 s, countdown, request at 10 s.
        self.hold(['settings_hint', 'warning_5', 'warning_1', 'setup_requested'])
        self.assertEqual(self.session.snapshot().state, 'hotspot')

    def test_countdown_says_the_setup_ends(self):
        self.start_from_idle()
        self.hold(['settings_hint', 'warning_3'])
        notice = self.menu.connect_notices.current(now=time.monotonic())
        self.assertEqual((notice.key, notice.params['seconds']), ('netsetup.cancel_warning', 3))
        self.assertEqual(self.session.snapshot().state, 'hotspot')

    def test_cancel_returns_to_overview_not_speaker_selection(self):
        self.start_from_idle()
        self.assertFalse(self.menu._netsetup_was_active)
        self.hold(['settings_hint', 'warning_5', 'warning_1', 'setup_requested'])
        self.assertIsNone(self.menu.netsetup_snapshot())
        self.assertEqual(self.menu.connect_notices.current(now=time.monotonic()).key,
                         'netsetup.cancelled')
        self.menu.poll(now=time.monotonic() + 10)   # end notice expired
        self.assertFalse(self.menu.active)

    def test_release_before_ten_seconds_keeps_setup(self):
        self.start_from_idle()
        self.hold(['settings_hint', 'warning_4', 'settings'])
        self.assertEqual(self.session.snapshot().state, 'hotspot')
        self.assertIsNone(self.menu.connect_notices.current(now=time.monotonic()))
        self.assertEqual(self.menu.section, 'targets')     # network menu not opened
        self.menu.frame(lambda image: image)


if __name__ == '__main__':
    unittest.main()
