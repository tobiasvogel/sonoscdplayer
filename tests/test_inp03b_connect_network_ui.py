"""INP-03b offline contract: GPIO options, semantic gestures and UI notices."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from integrations.interfaces.connect_gesture import ConnectGesture
from integrations.interfaces.input_core import load_config, InputEngine
from integrations.interfaces.interface_input import (
    publish_connect_event, tick_connect_event, pins_for)
from sonos_ui.connect_notice import ConnectNoticeView
from sonos_ui.ui_event_bridge import encode_event, decode_event
from sonos_ui.interactive_menu import InteractiveMenu


EXAMPLE = Path(__file__).resolve().parents[1] / 'config' / 'interface.example.toml'


class ConnectInputTests(unittest.TestCase):
    def config(self, replacement=None):
        source = EXAMPLE.read_text(encoding='utf-8')
        if replacement is not None:
            source = source.replace('speaker_select = -1', replacement)
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'interface.toml'
            target.write_text(source, encoding='utf-8')
            return load_config(target)

    def test_unassigned_speaker_pin_variants(self):
        for value in ('speaker_select = -1', 'speaker_select = ""',
                      'speaker_select = "null"', ''):
            with self.subTest(value=value):
                cfg = self.config(value)
                self.assertNotIn('speaker_select', cfg.buttons)

    def test_separate_button_is_real_input(self):
        cfg = self.config('speaker_select = 19')
        self.assertEqual(cfg.buttons['speaker_select'].pin, 19)
        self.assertIn(19, pins_for(cfg))
        levels = {pin: 0 for pin in pins_for(cfg)}
        levels[cfg.encoder.switch.pin] = 1  # encoder switch is active low
        input_engine = InputEngine(cfg, levels, 0)
        levels[19] = 1
        self.assertIn(('speaker_select', 'pressed'), input_engine.sample(levels, 20))

    def test_invalid_or_duplicate_gpio_rejected(self):
        for value in ('speaker_select = 21', 'speaker_select = 28',
                      'speaker_select = true', 'speaker_select = "oops"',
                      'speaker_select = -2'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.config(value)

    def test_toml_literal_null_is_invalid(self):
        with self.assertRaises(Exception):
            self.config('speaker_select = null')


class ConnectGestureTests(unittest.TestCase):
    def test_short_combined_routes_speaker_menu(self):
        events = []
        gesture = ConnectGesture()
        callback = lambda *args: events.append(args)
        publish_connect_event(gesture, 'connect', 'pressed', 100, callback,
                              separate_speaker_button=False)
        publish_connect_event(gesture, 'connect', 'released', 300, callback,
                              separate_speaker_button=False)
        self.assertEqual(events, [('speaker_select', 'pressed')])

    def test_short_with_separate_speaker_routes_network(self):
        events = []
        gesture = ConnectGesture()
        callback = lambda *args: events.append(args)
        publish_connect_event(gesture, 'connect', 'pressed', 0, callback,
                              separate_speaker_button=True)
        publish_connect_event(gesture, 'connect', 'released', 200, callback,
                              separate_speaker_button=True)
        publish_connect_event(gesture, 'speaker_select', 'pressed', 201, callback,
                              separate_speaker_button=True)
        self.assertEqual(events, [('connect', 'status'), ('speaker_select', 'pressed')])

    def test_boundaries_and_warning_countdown(self):
        g = ConnectGesture()
        g.press(100)
        self.assertEqual(g.release(3099), 'status')
        g.press(0)
        self.assertEqual(g.release(3000), 'settings')
        g.press(0)
        self.assertIsNone(g.tick(4999))
        self.assertEqual([g.tick(x) for x in (5000, 6000, 7000, 8000, 9000)],
                         ['warning_5', 'warning_4', 'warning_3', 'warning_2', 'warning_1'])
        self.assertIsNone(g.tick(9500))
        self.assertEqual(g.release(9999), 'settings')
        g.press(0)
        self.assertEqual(g.release(10000), 'setup_requested')

    def test_warning_never_executes_network_action(self):
        events = []
        g = ConnectGesture()
        publish_connect_event(g, 'connect', 'pressed', 0,
                              lambda *event: events.append(event))
        for t in (5000, 6000, 7000, 8000, 9000):
            tick_connect_event(g, t, lambda *event: events.append(event))
        publish_connect_event(g, 'connect', 'released', 10000,
                              lambda *event: events.append(event))
        self.assertEqual(events[-1], ('connect', 'setup_requested'))
        self.assertEqual(len(events), 6)

    def test_repeated_and_orphaned_edges(self):
        g = ConnectGesture()
        self.assertIsNone(g.release(0))
        g.press(0)
        g.press(9000)
        self.assertEqual(g.release(10000), 'setup_requested')
        g.press(11000)
        self.assertEqual(g.release(11100), 'status')

    def test_event_bridge_is_restrictive(self):
        allowed = [('speaker_select', 'pressed'), ('connect', 'status'),
                   ('connect', 'settings'), ('connect', 'setup_requested')]
        allowed.extend(('connect', f'warning_{n}') for n in range(1, 6))
        for pair in allowed:
            with self.subTest(pair=pair):
                self.assertEqual(decode_event(encode_event(*pair)), pair)
        for pair in (('connect', 'released'), ('connect', 'reset_now'),
                     ('speaker_select', 'released')):
            self.assertIsNone(encode_event(*pair))


class ConnectNoticeTests(unittest.TestCase):
    def test_countdown_is_localized_and_persistent(self):
        from sonos_ui.notice import notice_text
        view = ConnectNoticeView()
        for sec in range(5, 0, -1):
            self.assertTrue(view.show(f'warning_{sec}', now=5-sec))
            current = view.current(now=6-sec)
            self.assertEqual(current.params['seconds'], sec)
            self.assertIn(str(sec), notice_text(current, 'de'))
            self.assertIn(str(sec), notice_text(current, 'en'))
            self.assertFalse(view.confirm(now=6-sec))
        view.show('setup_requested', now=6)
        self.assertIsNotNone(view.current(now=999))
        self.assertTrue(view.confirm(now=1000))
        self.assertIsNone(view.current(now=1000))

    def test_normal_menu_does_not_advance_inactive_notice_clock(self):
        menu = InteractiveMenu(discover=lambda **_: None)
        # Ordinary status refresh can use real monotonic time while existing
        # menu tests inject synthetic timestamps, without a CONNECT notice.
        menu.poll()
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=0))
        self.assertTrue(menu.active)
        menu.poll()
        self.assertTrue(menu.event('encoder', 'right', now=1))

    def test_menu_returns_to_previous_state(self):
        menu = InteractiveMenu(discover=lambda **_: None)
        self.assertFalse(menu.active)
        self.assertTrue(menu.event('connect', 'status', now=1))
        self.assertTrue(menu.active)
        self.assertFalse(menu.event('encoder', 'right', now=2))
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=3))
        self.assertFalse(menu.active)

    def test_speaker_select_opens_existing_menu(self):
        menu = InteractiveMenu(discover=lambda **_: None)
        self.assertTrue(menu.event('speaker_select', 'pressed', now=1))
        self.assertTrue(menu.active)
        self.assertFalse(menu.language_picker)
        self.assertIsNone(menu.connect_notices.current(now=1))

    def test_power_priority(self):
        menu = InteractiveMenu(discover=lambda **_: None,
                               power_action=lambda _: True)
        menu.event('connect', 'settings', now=1)
        self.assertTrue(menu.event('power', 'short', now=2))
        self.assertTrue(menu.power_menu)
        self.assertIsNone(menu.connect_notices.current(now=2))

    def test_notice_renderer_offscreen(self):
        from sonos_ui.notice_render import NoticeRenderer
        view = ConnectNoticeView()
        view.show('warning_5', now=1)
        image = NoticeRenderer().render(view.current(now=1), grayscale=True)
        self.assertEqual(image.size, (128, 128))
        self.assertEqual(image.mode, 'L')


if __name__ == '__main__':
    unittest.main()
