"""INP-03c: nested OLED navigation and nonblocking network status."""
import threading
import time
import unittest

from integrations.outputs.oled_status import image_to_frame
from sonos_ui.i18n import Translator
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.network_status import NetworkStatus


class NavigationTests(unittest.TestCase):
    def make_menu(self, **kwargs):
        return InteractiveMenu(discover=lambda **_: None, **kwargs)

    def choose(self, menu, index, now=1):
        for _ in range(index):
            self.assertTrue(menu.event('encoder', 'right', now=now))
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=now))

    def test_encoder_opens_main_and_connect_short_opens_targets(self):
        menu = self.make_menu()
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=0))
        self.assertEqual(menu.section, 'main')
        self.assertTrue(menu.active)
        self.assertTrue(menu.event('speaker_select', 'pressed', now=1))
        self.assertEqual(menu.section, 'targets')
        self.assertEqual(menu._targets_return, 'closed')

    def test_main_targets_back_returns_to_main(self):
        menu = self.make_menu()
        menu.event('encoder_sw', 'pressed', now=0)
        self.choose(menu, 0)
        self.assertEqual(menu.section, 'targets')
        # Directly focus the existing target menu's Back item.
        from dataclasses import replace
        menu.state = replace(menu.state,
                             focus=len(menu.state.topology.targets) + 2)
        menu.event('encoder_sw', 'pressed', now=2)
        self.assertTrue(menu.active)
        self.assertEqual(menu.section, 'main')

    def test_speaker_menu_has_only_apply_and_back_after_targets(self):
        from dataclasses import replace
        from sonos_ui.multi_target import MultiSelection, Topology
        from sonos_ui.multi_render import MultiRenderer
        from sonos_ui.i18n import Translator
        from PIL import Image

        state = MultiSelection(topology=Topology(()))
        self.assertEqual(state.action('next').focus, 1)
        self.assertEqual(state.action('next').action('next').focus, 1)
        self.assertEqual(state.action('next').action('confirm').notice, 'menu.back')
        # Old cached focus value still resolves safely to Back.
        self.assertEqual(replace(state, focus=2).action('confirm').notice, 'menu.back')
        self.assertNotEqual(Translator('de').tr('menu.language'), 'menu.language')
        image = MultiRenderer().render_multi(state)
        self.assertIsInstance(image, Image.Image)
        self.assertEqual(image.size, (128, 128))

    def test_speaker_back_from_main_without_language_entry(self):
        from dataclasses import replace
        from sonos_ui.multi_target import Topology
        menu = self.make_menu()
        menu.event('encoder_sw', 'pressed', now=0)
        self.choose(menu, 0)
        menu.state = replace(menu.state, topology=Topology(()), focus=1)
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=1))
        self.assertEqual(menu.section, 'main')
        self.assertFalse(menu.language_picker)

    def test_network_status_back_and_main_back(self):
        reads = []
        def read():
            reads.append(1)
            return NetworkStatus("unavailable", "connected", "wifi", True)
        menu = self.make_menu(network_reader=read)
        menu.event('encoder_sw', 'pressed', now=0)
        self.choose(menu, 1)
        self.assertEqual(menu.section, 'network')
        self.choose(menu, 0)
        self.assertEqual(menu.section, 'network_status')
        deadline = time.monotonic() + 2
        while menu.network_snapshot.preferred != 'wifi' and time.monotonic() < deadline:
            menu.poll(now=1)
            time.sleep(0.005)
        self.assertEqual(reads, [1])
        self.assertEqual(menu.network_snapshot.preferred, 'wifi')
        frame = menu.frame(image_to_frame)
        self.assertEqual(len(frame.data), 8192)
        menu.event('encoder_sw', 'pressed', now=2)
        self.assertEqual(menu.section, 'network')
        self.choose(menu, 2, now=3)
        self.assertEqual(menu.section, 'main')

    def test_settings_language_picker_from_main(self):
        menu = self.make_menu()
        menu.event('encoder_sw', 'pressed', now=0)
        self.choose(menu, 2)
        self.assertEqual(menu.section, 'settings')
        self.choose(menu, 0)
        self.assertTrue(menu.language_picker)
        menu.event('encoder_sw', 'pressed', now=2)
        self.assertFalse(menu.language_picker)
        self.assertEqual(menu.section, 'settings')

    def test_network_read_never_blocks_encoder_loop(self):
        release = threading.Event()
        entered = threading.Event()
        def read():
            entered.set()
            release.wait(2)
            return NetworkStatus("connected", "unknown", "ethernet", True)
        try:
            menu = self.make_menu(network_reader=read)
            menu.event('encoder_sw', 'pressed', now=0)
            self.choose(menu, 1)
            self.choose(menu, 0)
            self.assertTrue(entered.wait(1))
            self.assertTrue(menu.event('encoder_sw', 'pressed', now=2))
            self.assertEqual(menu.section, 'network')
        finally:
            release.set()

    def test_power_priority_over_network(self):
        menu = self.make_menu(network_reader=lambda: NetworkStatus(),
                              power_action=lambda _: True)
        menu.event('encoder_sw', 'pressed', now=0)
        self.choose(menu, 1)
        self.choose(menu, 0)
        self.assertTrue(menu.event('power', 'short', now=2))
        self.assertTrue(menu.power_menu)
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=3))
        # "Back" returns to the network status that was open before.
        self.assertFalse(menu.power_menu)
        self.assertTrue(menu.active)
        self.assertEqual(menu.section, 'network_status')

    def test_network_settings_is_preview_only(self):
        menu = self.make_menu()
        menu.event('encoder_sw', 'pressed', now=0)
        self.choose(menu, 1)
        self.choose(menu, 1)
        self.assertIsNotNone(menu.connect_notices.current(now=1))
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=2))
        self.assertEqual(menu.section, 'network')

    def test_localized_offscreen_navigation(self):
        menu = self.make_menu()
        for lang in ('de', 'en'):
            menu.language = lang
            menu.event('encoder_sw', 'pressed', now=0)
            for section in ('main', 'network', 'settings', 'network_status'):
                menu.section = section
                menu.active = True
                frame = menu.frame(image_to_frame)
                self.assertEqual(len(frame.data), 8192)
            menu.active = False
            for key in ('menu.main', 'menu.network', 'network.ethernet',
                        'network.wifi', 'network.preferred',
                        'network.state.connected', 'network.state.unknown',
                        'network.preferred.none', 'network.readonly'):
                self.assertNotEqual(Translator(lang).tr(key), key)


if __name__ == '__main__':
    unittest.main()
