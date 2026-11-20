"""WLAN setup via a temporary hotspot."""
import http.client
import subprocess
import unittest
from unittest import mock
from urllib.parse import urlencode

from sonoscdplayer.network_setup import nm as nm_module
from sonoscdplayer.network_setup.nm import (
    HOTSPOT_PROFILE, NEW_PRIORITY, OLDER_PRIORITY, Nm, NmError, WifiNetwork,
    profile_name, split_terse, valid_password, valid_ssid,
)
from sonoscdplayer.network_setup.session import (
    PASSWORD_ALPHABET, PASSWORD_LENGTH, SetupSession, SetupSnapshot, new_password,
)
from sonoscdplayer.network_setup.web import SetupWebServer, done_page, page
from sonoscdplayer.ui.i18n import Translator
from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu


class FakeRunner:
    """Records nmcli calls; answers by matching argument fragments."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, argv, **kwargs):
        self.calls.append(argv[1:])
        joined = ' '.join(argv[1:])
        for fragment, (code, out) in self.answers.items():
            if fragment in joined:
                return subprocess.CompletedProcess(argv, code, out, '')
        return subprocess.CompletedProcess(argv, 0, '', '')

    def joined(self):
        return [' '.join(c) for c in self.calls]


class NmTests(unittest.TestCase):
    def test_split_terse_honours_escapes(self):
        self.assertEqual(split_terse(r'My\:Net:80:WPA2'), ['My:Net', '80', 'WPA2'])

    def test_validation(self):
        self.assertTrue(valid_ssid('Küche'))
        self.assertFalse(valid_ssid(''))
        self.assertFalse(valid_ssid('x' * 33))
        self.assertTrue(valid_password(''))
        self.assertTrue(valid_password('12345678'))
        self.assertFalse(valid_password('1234567'))
        self.assertFalse(valid_password('ä' * 10))
        self.assertTrue(profile_name('Net').startswith('sonoscdplayer-wifi-'))

    def test_wifi_device_and_suffix(self):
        runner = FakeRunner({
            'DEVICE,TYPE': (0, 'eth0:ethernet\nwlan0:wifi\n'),
            'GENERAL.HWADDR': (0, 'DC:A6:32:12:AB:CD\n'),
        })
        nm = Nm(runner=runner)
        self.assertEqual(nm.wifi_device(), 'wlan0')
        self.assertEqual(nm.hardware_suffix('wlan0'), 'ABCD')

    def test_scan_dedupes_sorts_and_survives_failure(self):
        runner = FakeRunner({'wifi list': (0, 'A:40:WPA2\nB:90:\nA:70:WPA2\n:50:WPA2\n')})
        self.assertEqual(Nm(runner=runner).scan('wlan0'), [
            WifiNetwork('B', 90, False), WifiNetwork('A', 70, True)])
        self.assertEqual(Nm(runner=FakeRunner({'wifi list': (10, '')})).scan('wlan0'), [])

    def test_hotspot_is_temporary(self):
        runner = FakeRunner()
        Nm(runner=runner).start_hotspot('wlan0', 'SonosCDPlayer-ABCD', 'PASSWORD')
        calls = runner.joined()
        self.assertTrue(any('wifi hotspot ifname wlan0 con-name ' + HOTSPOT_PROFILE in c
                            for c in calls))
        self.assertTrue(any('connection.autoconnect no' in c for c in calls))

    def test_connect_prefers_new_profile_and_demotes_older_own_profiles(self):
        new = profile_name('Home')
        runner = FakeRunner({
            'NAME connection show': (0, f'{new}\nsonoscdplayer-wifi-old\nUser profile\n'),
        })
        Nm(runner=runner).connect('wlan0', 'Home', 'secret123')
        calls = runner.joined()
        add = next(c for c in calls if 'connection add' in c)
        self.assertIn(f'connection.autoconnect-priority {NEW_PRIORITY}', add)
        self.assertIn('wifi-sec.psk secret123', add)
        self.assertTrue(any(f'modify id sonoscdplayer-wifi-old connection.autoconnect-priority '
                            f'{OLDER_PRIORITY}' in c for c in calls))
        self.assertFalse(any('User profile' in c for c in calls if 'modify' in c))

    def test_failed_connect_removes_profile_and_hides_password(self):
        runner = FakeRunner({'connection up': (4, '')})
        with self.assertRaises(NmError) as caught:
            Nm(runner=runner).connect('wlan0', 'Home', 'secret123')
        self.assertNotIn('secret123', str(caught.exception))
        self.assertIn(f'connection delete id {profile_name("Home")}', runner.joined()[-1])

    def test_open_network_has_no_security_settings(self):
        runner = FakeRunner()
        Nm(runner=runner).connect('wlan0', 'Cafe', '')
        add = next(c for c in runner.joined() if 'connection add' in c)
        self.assertNotIn('wifi-sec', add)

    def test_hotspot_active(self):
        runner = FakeRunner({'--active': (0, f'Wired\n{HOTSPOT_PROFILE}\n')})
        self.assertTrue(Nm(runner=runner).hotspot_active())
        self.assertFalse(Nm(runner=FakeRunner({'--active': (0, 'Wired\n')})).hotspot_active())

    def test_runner_errors_become_nm_errors(self):
        def broken(*_a, **_k):
            raise FileNotFoundError('nmcli')
        with self.assertRaises(NmError):
            Nm(runner=broken).wifi_device()


class FakeNm:
    def __init__(self, *, device='wlan0', connect_ok=True, hotspot_ok=True):
        self.device = device
        self.connect_ok = connect_ok
        self.hotspot_ok = hotspot_ok
        self.log = []

    def wifi_device(self):
        return self.device

    def scan(self, device):
        return [WifiNetwork('Home', 80, True)]

    def hardware_suffix(self, device):
        return 'ABCD'

    def start_hotspot(self, device, ssid, password):
        self.log.append(('hotspot', ssid, password))
        if not self.hotspot_ok:
            raise NmError('hotspot failed')

    def hotspot_address(self, device):
        return '10.42.0.1'

    def stop_hotspot(self):
        self.log.append(('stop',))

    def connect(self, device, ssid, password, hidden=False):
        self.log.append(('connect', ssid, password) + (('hidden',) if hidden else ()))
        if not self.connect_ok:
            raise NmError('could not connect')

    def saved_profiles(self):
        return dict(getattr(self, 'saved', {}))

    def connect_saved(self, device, ssid):
        self.log.append(('saved', ssid))
        if not getattr(self, 'saved_ok', True):
            raise NmError('saved profile failed')


class FakeWeb:
    def __init__(self, session, address):
        self.url = f'http://{address}'
        self.stopped = False

    def stop(self):
        self.stopped = True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_session(nm=None, **kwargs):
    clock = Clock()
    webs = []

    def web(session, address):
        webs.append(FakeWeb(session, address))
        return webs[-1]
    session = SetupSession(nm or FakeNm(), clock=clock, web_factory=web,
                           password_factory=lambda: 'K7M3Q9TX', log=lambda *_: None,
                           sleep=lambda _s: None, **kwargs)
    return session, clock, webs


class SessionTests(unittest.TestCase):
    def test_password_alphabet_is_readable(self):
        password = new_password()
        self.assertEqual(len(password), PASSWORD_LENGTH)
        self.assertTrue(set(password) <= set(PASSWORD_ALPHABET))
        self.assertFalse(set('01OIL') & set(PASSWORD_ALPHABET))

    def test_start_scans_then_opens_hotspot(self):
        session, _clock, webs = make_session()
        self.assertTrue(session.start())
        session.join()
        snap = session.snapshot()
        self.assertEqual((snap.state, snap.hotspot_ssid, snap.password, snap.url),
                         ('hotspot', 'SonosCDPlayer-ABCD', 'K7M3Q9TX', 'http://10.42.0.1'))
        self.assertEqual(snap.networks, (WifiNetwork('Home', 80, True),))
        self.assertEqual(snap.remaining, 900)
        self.assertEqual(len(webs), 1)
        self.assertFalse(session.start())  # already running

    def test_timeout_ends_hotspot(self):
        nm = FakeNm()
        session, clock, webs = make_session(nm)
        session.start()
        session.join()
        clock.now = 899
        session.tick()
        self.assertEqual(session.snapshot().remaining, 1)
        clock.now = 900
        session.tick()
        session.join()
        self.assertEqual(session.snapshot().state, 'idle')
        self.assertTrue(webs[0].stopped)
        self.assertEqual(nm.log[-1], ('stop',))

    def test_cancel(self):
        nm = FakeNm()
        session, _clock, webs = make_session(nm)
        session.start()
        session.join()
        self.assertTrue(session.cancel())
        session.join()
        self.assertFalse(session.active)
        self.assertTrue(webs[0].stopped)
        self.assertFalse(session.cancel())

    def test_submit_validates_and_connects(self):
        nm = FakeNm()
        session, clock, _webs = make_session(nm)
        self.assertEqual(session.submit('Home', 'secret123'), 'not_ready')
        session.start()
        session.join()
        self.assertEqual(session.submit('', 'secret123'), 'invalid_ssid')
        self.assertEqual(session.submit('Home', 'short'), 'invalid_password')
        self.assertEqual(session.submit('Home', 'secret123'), '')
        session.join()
        self.assertIn(('connect', 'Home', 'secret123'), nm.log)
        snap = session.snapshot()
        self.assertEqual((snap.state, snap.target, snap.password), ('connected', 'Home', ''))
        clock.now += 10
        session.tick()
        self.assertEqual(session.snapshot().state, 'idle')

    def test_failed_connect_reopens_same_hotspot(self):
        nm = FakeNm(connect_ok=False)
        session, _clock, webs = make_session(nm)
        session.start()
        session.join()
        session.submit('Home', 'wrongpass')
        session.join()
        snap = session.snapshot()
        self.assertEqual((snap.state, snap.error, snap.password),
                         ('hotspot', 'connect_failed', 'K7M3Q9TX'))
        self.assertEqual([e for e in nm.log if e[0] == 'hotspot'],
                         [('hotspot', 'SonosCDPlayer-ABCD', 'K7M3Q9TX')] * 2)
        self.assertEqual(len(webs), 2)
        self.assertTrue(webs[0].stopped)

    def test_no_wifi_and_hotspot_failure_are_shown_then_cleared(self):
        for nm, error in ((FakeNm(device=None), 'no_wifi'),
                          (FakeNm(hotspot_ok=False), 'hotspot_failed')):
            with self.subTest(error=error):
                session, clock, _webs = make_session(nm)
                session.start()
                session.join()
                self.assertEqual((session.snapshot().state, session.snapshot().error),
                                 ('unavailable', error))
                clock.now += 10
                session.tick()
                self.assertEqual(session.snapshot().state, 'idle')
                self.assertTrue(session.start())  # retry possible


class StubSession:
    def __init__(self, snap):
        self.snap = snap
        self.submitted = []

    def snapshot(self):
        return self.snap

    def submit(self, ssid, password):
        self.submitted.append((ssid, password))
        return '' if password != 'bad' else 'invalid_password'


HOTSPOT = SetupSnapshot(state='hotspot', hotspot_ssid='SonosCDPlayer-ABCD',
                        password='K7M3Q9TX', networks=(WifiNetwork('<b>Net</b>', 70, True),))


class WebTests(unittest.TestCase):
    def test_page_escapes_network_names(self):
        html = page(Translator('de'), HOTSPOT)
        self.assertIn('&lt;b&gt;Net&lt;/b&gt;', html)
        self.assertNotIn('<b>Net</b>', html)
        self.assertNotIn('K7M3Q9TX', html)  # the hotspot password is not repeated
        self.assertIn('lang="de"', html)
        self.assertIn('&lt;x&gt;', done_page(Translator('en'), '<x>'))

    def request(self, server, method, body=None, headers=None):
        host, port = server._server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(method, '/generate_204', body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read().decode('utf-8')
        conn.close()
        return response.status, data

    def test_server_get_and_post(self):
        session = StubSession(HOTSPOT)
        server = SetupWebServer(session, '127.0.0.1', Translator('en'), ports=(0,))
        try:
            self.assertTrue(server.url.startswith('http://127.0.0.1:'))
            status, html = self.request(server, 'GET')
            self.assertEqual(status, 200)
            self.assertIn('name="password"', html)
            form = {'Content-Type': 'application/x-www-form-urlencoded'}
            status, _ = self.request(server, 'POST', urlencode(
                {'ssid': 'Home', 'hidden': '', 'password': 'secret123'}), form)
            self.assertEqual(status, 200)
            status, _ = self.request(server, 'POST', urlencode(
                {'ssid': 'Home', 'hidden': ' Hidden ', 'password': 'bad'}), form)
            self.assertEqual(status, 400)
            self.assertEqual(session.submitted, [('Home', 'secret123'), ('Hidden', 'bad')])
            status, _ = self.request(server, 'POST', 'x' * 3000, form)
            self.assertEqual(status, 400)
            self.assertEqual(len(session.submitted), 2)
        finally:
            server.stop()

    def test_url_omits_port_80(self):
        fake = mock.MagicMock()
        fake.server_address = ('10.42.0.1', 80)
        with mock.patch('sonoscdplayer.network_setup.web.ThreadingHTTPServer', return_value=fake), \
                mock.patch('sonoscdplayer.network_setup.web.threading.Thread'):
            server = SetupWebServer(StubSession(HOTSPOT), '10.42.0.1', Translator('de'))
        self.assertEqual(server.url, 'http://10.42.0.1')


class MenuSession:
    def __init__(self):
        self.snap = SetupSnapshot()
        self.started = self.cancelled = 0

    def snapshot(self):
        return self.snap

    @property
    def active(self):
        return self.snap.state != 'idle'

    def start(self):
        self.started += 1
        self.snap = HOTSPOT
        return True

    def cancel(self):
        self.cancelled += 1
        self.snap = SetupSnapshot()
        return True

    def tick(self):
        pass


class MenuIntegrationTests(unittest.TestCase):
    def make(self):
        setup = MenuSession()
        return InteractiveMenu(discover=lambda **_: None, network_setup=setup), setup

    def test_connect_ten_seconds_starts_and_again_cancels(self):
        menu, setup = self.make()
        self.assertTrue(menu.event('connect', 'setup_requested', now=1))
        self.assertEqual(setup.started, 1)
        self.assertEqual(menu.netsetup_snapshot().state, 'hotspot')
        self.assertFalse(menu.event('encoder', 'right', now=2))  # input swallowed
        self.assertTrue(menu.event('connect', 'setup_requested', now=3))
        self.assertEqual(setup.cancelled, 1)
        self.assertIsNone(menu.netsetup_snapshot())
        self.assertEqual(menu.connect_notices.current(now=3).key, 'netsetup.cancelled')
        menu.poll(now=10)
        self.assertFalse(menu.active)

    def test_countdown_notice_does_not_stick_after_setup(self):
        menu, setup = self.make()
        menu.event('connect', 'warning_1', now=1)
        self.assertTrue(menu.active)
        menu.event('connect', 'setup_requested', now=2)
        self.assertIsNone(menu.connect_notices.current(now=2))
        setup.snap = SetupSnapshot()
        menu.poll(now=3)
        self.assertFalse(menu.active)

    def test_blocked_during_playback(self):
        menu, setup = self.make()
        menu.player_mode = 'PLAYING'
        self.assertTrue(menu.event('connect', 'setup_requested', now=1))
        self.assertEqual(setup.started, 0)

    def test_connect_settings_opens_network_menu_and_item_starts_setup(self):
        menu, setup = self.make()
        self.assertTrue(menu.event('connect', 'settings', now=1))
        self.assertEqual((menu.section, menu.menu_focus), ('network', 1))
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=2))
        self.assertEqual(setup.started, 1)

    def test_setup_screen_renders(self):
        from sonoscdplayer.ui.display_service import image_to_frame
        menu, _setup = self.make()
        menu.event('connect', 'setup_requested', now=1)
        self.assertEqual(len(menu.frame(image_to_frame).data), 8192)

    def test_back_to_previous_screen_when_setup_ends(self):
        menu, setup = self.make()
        menu.event('connect', 'setup_requested', now=1)
        setup.snap = SetupSnapshot()
        menu.poll(now=2)
        self.assertFalse(menu.active)

    def test_oled_gives_setup_screen_priority_over_drive_notices(self):
        from sonoscdplayer.ui import display_service as oled_status
        menu, _setup = self.make()
        self.assertFalse(oled_status.netsetup_shown(menu))
        menu.event('connect', 'setup_requested', now=1)
        self.assertTrue(oled_status.netsetup_shown(menu))
        self.assertFalse(oled_status.netsetup_shown(mock.MagicMock()))
        with mock.patch.object(oled_status, 'drive_notice_frame') as drive:
            frame = oled_status.selected_status_frame({}, menu=menu)
        drive.assert_not_called()
        self.assertEqual(len(frame.data), 8192)


class RenderTests(unittest.TestCase):
    def test_wrap_keeps_ellipsis_and_fits(self):
        from sonoscdplayer.ui.renderers.oled_128_speakers import fit_size, text_width, wrap_lines
        lines = wrap_lines('Verbinde mit dem Netzwerk Wohnzimmer …', 116, 11)
        self.assertNotEqual(lines[-1], '…')
        self.assertTrue(lines[-1].endswith(' …'))
        self.assertTrue(all(text_width(line, 11) <= 116 for line in lines))
        self.assertLessEqual(text_width('SonosCDPlayer-ABCD', fit_size('SonosCDPlayer-ABCD', 118, 10)), 118)


class LedHotspotTests(unittest.TestCase):
    def test_hotspot_reported_on_connection_led(self):
        from sonoscdplayer.hardware.leds import led_service as led_pwm
        from sonoscdplayer.hardware.leds.model import Network
        with mock.patch('sonoscdplayer.ui.menu.network_status.read_network_status'), \
                mock.patch.object(led_pwm, 'classify_network', return_value=Network.CONNECTED), \
                mock.patch.object(nm_module.Nm, 'hotspot_active', return_value=True):
            self.assertEqual(led_pwm.read_network(), Network.HOTSPOT)
        with mock.patch('sonoscdplayer.ui.menu.network_status.read_network_status'), \
                mock.patch.object(led_pwm, 'classify_network', return_value=Network.CONNECTED), \
                mock.patch.object(nm_module.Nm, 'hotspot_active', side_effect=NmError('x')):
            self.assertEqual(led_pwm.read_network(), Network.CONNECTED)


if __name__ == '__main__':
    unittest.main()
