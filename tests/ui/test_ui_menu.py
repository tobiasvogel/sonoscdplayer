"""On-device menu: encoder navigation, nested pages and the cached speaker list."""
import threading
import time
import unittest
from types import SimpleNamespace as NS
from sonoscdplayer.ui.menu.interactive_menu import (
    InteractiveMenu,
    EMPTY_SCAN_RETRY_SECONDS,
    SCAN_RESULT_SECONDS,
)
from sonoscdplayer.ui.menu.speakers.multi_target import from_devices, Target, Topology
from sonoscdplayer.ui.display_service import image_to_frame
from sonoscdplayer.ui.i18n import Translator
from sonoscdplayer.ui.menu.network_status import NetworkStatus
from sonoscdplayer.ui.menu.navigation_pages import SETTINGS_ITEMS
from sonoscdplayer.ui.notices.notice import notice_text
TWO = Topology((Target('A', 'Küche', ('A',), ('Küche',)),
                Target('B', 'Wohnzimmer', ('B',), ('Wohnzimmer',))))



def topology():
    a=NS(uid='one',player_name='Gästezimmer')
    b=NS(uid='two',player_name='Wohnzimmer')
    g=NS(coordinator=a,members=(a,b))
    a.group=b.group=g
    return from_devices((a,b))


class InteractiveMenuTests(unittest.TestCase):
    def make_menu(self):
        return InteractiveMenu(discover=lambda *,timeout: topology())

    def test_closed_menu_ignores_encoder(self):
        menu=self.make_menu()
        self.assertFalse(menu.event('encoder','right'))
        self.assertFalse(menu.active)

    def test_encoder_opens_main_and_back_closes_menu(self):
        menu=self.make_menu()
        self.assertTrue(menu.event('encoder_sw','pressed',now=0))
        self.assertTrue(menu.active)
        self.assertEqual(menu.section, 'main')
        for _ in range(3):
            menu.event('encoder','right',now=1)
        self.assertTrue(menu.event('encoder_sw','pressed',now=2))
        self.assertFalse(menu.active)

    def test_encoder_selects_only_local_draft(self):
        menu=self.make_menu()
        menu.event('speaker_select','pressed',now=0)
        menu.state=menu.state.update(topology())
        self.assertTrue(menu.event('encoder_sw','pressed'))
        self.assertEqual(len(menu.state.draft),1)
        self.assertFalse(menu.state.applied)

    def test_encoder_moves_focus(self):
        menu=self.make_menu()
        menu.event('speaker_select','pressed',now=0)
        menu.state=menu.state.update(topology())
        menu.event('encoder','right')
        self.assertEqual(menu.state.focus,1)

    def test_back_cancels_unconfirmed_selection(self):
        menu=self.make_menu()
        menu.event('speaker_select','pressed',now=0)
        menu.state=menu.state.update(topology())
        menu.event('encoder_sw','pressed')
        menu.state=__import__('dataclasses').replace(menu.state,focus=len(menu.state.topology.targets)+2)
        menu.event('encoder_sw','pressed',now=1)
        self.assertFalse(menu.active)
        self.assertEqual(menu.state.draft,menu.state.applied)

    def test_release_event_is_ignored(self):
        menu=self.make_menu()
        menu.event('encoder_sw','pressed',now=0)
        self.assertFalse(menu.event('encoder_sw','released'))

    def test_render_valid_frame(self):
        from sonoscdplayer.ui.display_service import image_to_frame
        menu=self.make_menu()
        menu.event('encoder_sw','pressed',now=0)
        menu.state=menu.state.update(topology())
        frame=menu.frame(image_to_frame)
        self.assertEqual(len(frame.data),8192)


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
        from sonoscdplayer.ui.menu.speakers.multi_target import MultiSelection, Topology
        from sonoscdplayer.ui.renderers.oled_128_speakers import MultiRenderer
        from sonoscdplayer.ui.i18n import Translator
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
        from sonoscdplayer.ui.menu.speakers.multi_target import Topology
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


class Discover:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, **_kwargs):
        self.calls += 1
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def settle(menu, now):
    if menu._worker is not None:
        menu._worker.join(timeout=2)
    menu.poll(now=now)


class SpeakerScanCacheTests(unittest.TestCase):
    def test_discovers_once_at_start_and_reuses_the_list(self):
        discover = Discover(TWO)
        menu = InteractiveMenu(discover=discover)
        menu.poll(now=0)
        settle(menu, 1)
        self.assertEqual(discover.calls, 1)
        for now in (2, 100, 1000):
            menu.event('speaker_select', 'pressed', now=now)   # open list
            self.assertEqual(len(menu.state.topology.targets), 2)
            menu.active = False
            settle(menu, now + 1)
        self.assertEqual(discover.calls, 1, 'opening the list never rescans')

    def test_empty_result_is_retried_quietly_and_on_open(self):
        discover = Discover(Topology((), 'Timeout'), TWO)
        menu = InteractiveMenu(discover=discover)
        menu.poll(now=0)
        settle(menu, 1)
        self.assertEqual(discover.calls, 1)
        settle(menu, EMPTY_SCAN_RETRY_SECONDS - 1)
        self.assertEqual(discover.calls, 1)
        menu.poll(now=EMPTY_SCAN_RETRY_SECONDS + 1)
        settle(menu, EMPTY_SCAN_RETRY_SECONDS + 2)
        self.assertEqual(discover.calls, 2)
        self.assertEqual(len(menu.state.topology.targets), 2)
        settle(menu, 10 * EMPTY_SCAN_RETRY_SECONDS)
        self.assertEqual(discover.calls, 2, 'no retries once speakers are known')

    def open_settings_item(self, menu, index, now):
        menu.event('encoder_sw', 'pressed', now=now)          # main menu
        for _ in range(2):
            menu.event('encoder', 'right', now=now)
        menu.event('encoder_sw', 'pressed', now=now)          # settings
        for _ in range(index):
            menu.event('encoder', 'right', now=now)
        menu.event('encoder_sw', 'pressed', now=now)

    def test_search_targets_forces_a_scan_with_progress_and_result(self):
        three = Topology(TWO.targets + (Target('C', 'Bad', ('C',), ('Bad',)),))
        discover = Discover(TWO, three)
        menu = InteractiveMenu(discover=discover)
        menu.poll(now=0)
        settle(menu, 1)
        self.assertEqual(SETTINGS_ITEMS.index('menu.rescan'), 1)
        self.open_settings_item(menu, 1, now=2)
        running = menu.connect_notices.current(now=2)
        self.assertEqual(running.key, 'status.scan_running')
        self.assertFalse(running.dismissible)
        self.assertFalse(menu.event('encoder_sw', 'pressed', now=2.5))
        settle(menu, 3)
        self.assertEqual(discover.calls, 2)
        done = menu.connect_notices.current(now=3)
        self.assertEqual((done.key, done.severity), ('status.scan_done', 'success'))
        self.assertEqual(notice_text(done, 'de'), '3 Lautsprecher gefunden')
        self.assertEqual(notice_text(done, 'en'), '3 speakers found')
        from sonoscdplayer.ui.i18n import Translator
        self.assertEqual(Translator('de').tr('menu.rescan'), 'Lautsprecher suchen')
        menu.poll(now=3 + SCAN_RESULT_SECONDS)
        self.assertEqual((menu.active, menu.section), (True, 'settings'))
        self.assertEqual(len(menu.state.topology.targets), 3)

    def test_search_targets_reports_nothing_found(self):
        discover = Discover(TWO, Topology((), 'Timeout'))
        menu = InteractiveMenu(discover=discover)
        menu.poll(now=0)
        settle(menu, 1)
        self.open_settings_item(menu, 1, now=2)
        settle(menu, 3)
        current = menu.connect_notices.current(now=3)
        self.assertEqual((current.key, current.severity), ('status.scan_none', 'warning'))

    def test_settings_back_is_the_last_entry(self):
        menu = InteractiveMenu(discover=Discover(TWO))
        self.open_settings_item(menu, 3, now=0)
        self.assertEqual((menu.section, menu.menu_focus), ('main', 2))


if __name__ == '__main__':
    unittest.main()
