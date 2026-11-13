"""Offline tests for the power button: gesture, menu, privileged command, polkit rule."""
import subprocess
import re
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path

from integrations.interfaces.power_gesture import PowerCoordinator, PowerGesture
from integrations.interfaces import interface_input
from integrations.interfaces.input_core import load_config
from power_action import COMMANDS, PowerRequester
from sonos_ui import ui_event_bridge
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.i18n import Translator

ROOT = Path(__file__).resolve().parents[1]


class GestureTests(unittest.TestCase):
    def test_short_press(self):
        g = PowerGesture()
        g.press(0)
        self.assertIsNone(g.tick(2999))
        self.assertEqual(g.release(2999), 'short')

    def test_long_press_fires_once_and_release_is_silent(self):
        g = PowerGesture()
        g.press(1000)
        self.assertIsNone(g.tick(3999))
        self.assertEqual(g.tick(4000), 'long')
        self.assertIsNone(g.tick(9000))
        self.assertIsNone(g.release(9500))

    def test_release_without_press_is_ignored(self):
        self.assertIsNone(PowerGesture().release(10))

    def test_hold_time_cannot_be_shorter_than_three_seconds(self):
        for bad in (0, 2999, 10001, True, '3000', 3000.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PowerGesture(bad)

    def test_second_press_after_long_press_starts_fresh(self):
        g = PowerGesture()
        g.press(0)
        g.tick(3000)
        g.release(3100)
        g.press(4000)
        self.assertEqual(g.release(4100), 'short')


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.published, self.shutdowns = [], []
        self.c = PowerCoordinator(3000, publish=lambda *a: self.published.append(a),
                                  shutdown=lambda: self.shutdowns.append(1))

    def test_short_press_goes_to_ui_not_to_shutdown(self):
        self.c.event('power', 'pressed', 0)
        self.c.tick(500)
        self.c.event('power', 'released', 500)
        self.assertEqual((self.published, self.shutdowns), ([('power', 'short')], []))

    def test_long_press_shuts_down_once_without_ui_event(self):
        self.c.event('power', 'pressed', 0)
        for t in range(0, 6000, 100):
            self.c.tick(t)
        self.c.event('power', 'released', 6000)
        self.assertEqual((self.published, self.shutdowns), ([], [1]))

    def test_other_buttons_are_ignored(self):
        for name in ('stop', 'eject', 'encoder_sw'):
            self.c.event(name, 'pressed', 0)
            self.c.tick(10000)
            self.c.event(name, 'released', 10000)
        self.assertEqual((self.published, self.shutdowns), ([], []))


class RequesterTests(unittest.TestCase):
    def make(self, code=0, stderr='', exc=None):
        calls = []

        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if exc:
                raise exc
            return subprocess.CompletedProcess(cmd, code, '', stderr)
        return PowerRequester(run=run, log=lambda *a, **k: None), calls

    def test_runs_fixed_argv_without_shell(self):
        for action, last in (('poweroff', 'poweroff'), ('reboot', 'reboot')):
            req, calls = self.make()
            self.assertTrue(req.request(action))
            cmd, kwargs = calls[0]
            self.assertEqual(cmd, ['/usr/bin/systemctl', '--no-ask-password', last])
            self.assertNotIn('shell', kwargs)
            self.assertIn('timeout', kwargs)

    def test_unknown_action_is_rejected_before_running_anything(self):
        req, calls = self.make()
        for bad in ('halt', 'poweroff; id', '', None, 'suspend'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                req.request(bad)
        self.assertEqual(calls, [])
        self.assertEqual(set(COMMANDS), {'poweroff', 'reboot'})

    def test_accepted_request_is_issued_only_once(self):
        req, calls = self.make()
        self.assertTrue(req.request('poweroff'))
        self.assertFalse(req.request('reboot'))
        self.assertFalse(req.request('poweroff'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(req.issued, 'poweroff')

    def test_refused_request_can_be_retried(self):
        req, calls = self.make(code=1, stderr='Access denied\nmore')
        self.assertFalse(req.request('poweroff'))
        self.assertFalse(req.request('poweroff'))
        self.assertEqual(len(calls), 2)
        self.assertIsNone(req.issued)

    def test_timeout_and_missing_binary_are_failures_not_exceptions(self):
        for exc in (subprocess.TimeoutExpired('x', 1), FileNotFoundError()):
            req, _ = self.make(exc=exc)
            self.assertFalse(req.request('reboot'))

    def test_concurrent_requests_run_once(self):
        gate = threading.Event()
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            gate.wait(1)
            return subprocess.CompletedProcess(cmd, 0, '', '')
        req = PowerRequester(run=run, log=lambda *a, **k: None)
        threads = [threading.Thread(target=req.request, args=('poweroff',)) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        gate.set()
        for t in threads:
            t.join(2)
        self.assertEqual(len(calls), 1)


class ShutdownCallbackTests(unittest.TestCase):
    def test_preview_mode_never_touches_the_system(self):
        class Boom:
            def request(self, action):
                raise AssertionError('must not run')
        interface_input.make_power_shutdown(False, requester=Boom(), start=lambda t: t())()

    def test_enabled_long_press_requests_poweroff_off_thread(self):
        seen = []

        class Req:
            def request(self, action):
                seen.append(action)
        started = []
        cb = interface_input.make_power_shutdown(True, requester=Req(), start=started.append)
        cb()
        self.assertEqual(seen, [])        # handed to a worker, not run on the GPIO loop
        started[0]()
        self.assertEqual(seen, ['poweroff'])


class MenuTests(unittest.TestCase):
    def make(self, result=True):
        self.calls = []
        self.done = threading.Event()

        def action(choice):
            self.calls.append(choice)
            self.done.set()
            return result
        return InteractiveMenu(discover=lambda *, timeout: None, power_action=action)

    def test_short_press_opens_with_back_focused(self):
        m = self.make()
        self.assertTrue(m.event('power', 'short'))
        self.assertTrue(m.active and m.power_menu)
        self.assertEqual(m.power_focus, 0)

    def test_without_power_action_the_press_is_ignored(self):
        m = InteractiveMenu(discover=lambda *, timeout: None)
        self.assertFalse(m.event('power', 'short'))
        self.assertFalse(m.active)

    def test_back_closes_without_action(self):
        m = self.make()
        m.event('power', 'short')
        self.assertTrue(m.event('encoder_sw', 'pressed'))
        self.assertFalse(m.active or m.power_menu)
        self.assertEqual(self.calls, [])

    def test_second_short_press_closes(self):
        m = self.make()
        m.event('power', 'short')
        m.event('power', 'short')
        self.assertFalse(m.active or m.power_menu)

    def test_focus_is_clamped_and_choice_runs_action(self):
        for steps, expected in ((1, 'poweroff'), (2, 'reboot'), (5, 'reboot')):
            with self.subTest(expected=expected):
                m = self.make()
                m.event('power', 'short')
                m.event('encoder', 'left')
                for _ in range(steps):
                    m.event('encoder', 'right')
                m.event('encoder_sw', 'pressed')
                self.assertTrue(self.done.wait(2))
                self.assertEqual(self.calls, [expected])
                self.assertEqual(m.power_notice, 'status.power_' + expected)

    def test_input_is_ignored_while_the_action_runs(self):
        m = self.make()
        m.event('power', 'short')
        m.event('encoder', 'right')
        m.event('encoder_sw', 'pressed')
        self.assertTrue(self.done.wait(2))
        self.assertFalse(m.event('encoder', 'right'))
        self.assertFalse(m.event('power', 'short'))
        self.assertTrue(m.power_menu)
        self.assertEqual(self.calls, ['poweroff'])

    def test_failed_action_reopens_the_menu_for_another_try(self):
        m = self.make(result=False)
        m.event('power', 'short')
        m.event('encoder', 'right')
        m.event('encoder_sw', 'pressed')
        self.assertTrue(self.done.wait(2))
        deadline = time.monotonic() + 2
        while m._power_busy and time.monotonic() < deadline:
            m.poll(now=0)
            time.sleep(0.01)
        self.assertFalse(m._power_busy)
        self.assertEqual(m.power_notice, 'status.power_failed')
        self.assertTrue(m.event('encoder', 'right'))

    def test_power_menu_overlays_and_returns_to_previous_menu(self):
        m = self.make()
        m.event('encoder_sw', 'pressed', now=0)       # main menu
        m.event('encoder', 'right', now=0)            # focus "network"
        m.event('power', 'short')
        self.assertTrue(m.power_menu)
        m.event('power', 'short')                     # second short press = back
        self.assertTrue(m.active and not m.power_menu)
        self.assertEqual((m.section, m.menu_focus), ('main', 1))

    def test_language_picker_returns_to_its_menu_entry(self):
        m = self.make()
        m.event('encoder_sw', 'pressed', now=0)
        m.section, m.menu_focus = 'settings', 0
        m.language_picker = True
        m.event('power', 'short')
        self.assertTrue(m.power_menu and not m.language_picker)
        m.event('encoder_sw', 'pressed')              # "back"
        self.assertFalse(m.language_picker)
        self.assertEqual((m.section, m.menu_focus), ('settings', 0))

    def test_overview_stays_overview_after_power_menu(self):
        m = self.make()
        m.event('power', 'short')
        m.event('encoder_sw', 'pressed')
        self.assertFalse(m.active or m.power_menu)

    def test_target_menu_still_works_afterwards(self):
        m = self.make()
        m.event('power', 'short')
        m.event('power', 'short')
        self.assertTrue(m.event('encoder_sw', 'pressed', now=0))
        self.assertTrue(m.active and not m.power_menu)

    def test_frame_renders_power_menu(self):
        m = self.make()
        m.event('power', 'short')
        frames = []
        m.frame(lambda image: frames.append(image) or image)
        self.assertEqual(frames[0].size, (128, 128))

    def test_menu_text_exists_in_every_language(self):
        keys = ('menu.power', 'menu.poweroff', 'menu.reboot', 'menu.back', 'status.power_choose',
                'status.power_poweroff', 'status.power_reboot', 'status.power_failed', 'footer.select')
        for lang in ('de', 'en'):
            tr = Translator(lang).tr
            for key in keys:
                self.assertNotEqual(tr(key), key, (lang, key))


class WiringTests(unittest.TestCase):
    def test_ui_bridge_carries_only_the_short_press(self):
        self.assertEqual(ui_event_bridge.decode_event(
            ui_event_bridge.encode_event('power', 'short')), ('power', 'short'))
        for event in ('pressed', 'released', 'long'):
            self.assertIsNone(ui_event_bridge.encode_event('power', event))

    def test_interface_config_hold_time(self):
        cfg = load_config(ROOT / 'config/interface.example.toml')
        self.assertEqual(cfg.power_hold_ms, 3000)
        with tempfile.TemporaryDirectory() as tmp:
            for value, ok in (('2999', False), ('3000', True), ('10000', True), ('"x"', False)):
                path = Path(tmp) / 'i.toml'
                text = (ROOT / 'config/interface.example.toml').read_text()
                path.write_text(re.sub(r'long_press_ms = \d+', f'long_press_ms = {value}', text))
                with self.subTest(value=value):
                    if ok:
                        self.assertEqual(load_config(path).power_hold_ms, int(value))
                    else:
                        with self.assertRaises(ValueError):
                            load_config(path)

    def test_polkit_rule_grants_only_poweroff_and_reboot_to_pi(self):
        text = (ROOT / 'systemd/polkit/50-sonoscdplayer-power.rules').read_text()
        code = '\n'.join(l for l in text.splitlines() if not l.strip().startswith('//'))
        actions = set(re.findall(r'"(org\.freedesktop\.[a-z0-9.-]+)"', code))
        self.assertEqual(actions, {
            'org.freedesktop.login1.power-off', 'org.freedesktop.login1.power-off-multiple-sessions',
            'org.freedesktop.login1.reboot', 'org.freedesktop.login1.reboot-multiple-sessions'})
        self.assertIn('subject.user === "@SONOSCDPLAYER_USER@"', code)
        self.assertNotIn('ignore-inhibit', code)

    def test_rendered_units_enable_both_switches(self):
        from tools.render_runtime_units import render
        units = render(ROOT / 'systemd', checkout='/opt/example/src',
                       python_venv='/opt/example/.venv/bin/python',
                       user='example')
        self.assertIn('--power-actions', units['sonoscdplayer-buttons.service'])
        self.assertIn('--ui-power-actions', units['sonoscdplayer-oled.service'])

    def test_player_turns_sigterm_into_the_clean_shutdown_path(self):
        import player_service
        previous = signal.getsignal(signal.SIGTERM)
        try:
            player_service.install_sigterm_handler()
            self.assertIs(signal.getsignal(signal.SIGTERM), player_service._terminate)
            with self.assertRaises(KeyboardInterrupt):
                player_service._terminate(signal.SIGTERM, None)
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    unittest.main()
