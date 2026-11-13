"""Speaker list: discovered once at start, cached, manual "Find speakers"."""
import unittest

from sonos_ui.interactive_menu import (EMPTY_SCAN_RETRY_SECONDS, SCAN_RESULT_SECONDS,
                                       InteractiveMenu)
from sonos_ui.multi_target import Target, Topology
from sonos_ui.navigation_pages import SETTINGS_ITEMS
from sonos_ui.notice import notice_text

TWO = Topology((Target('A', 'Küche', ('A',), ('Küche',)),
                Target('B', 'Wohnzimmer', ('B',), ('Wohnzimmer',))))


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
        from sonos_ui.i18n import Translator
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

    def test_settings_back_is_now_third_entry(self):
        menu = InteractiveMenu(discover=Discover(TWO))
        self.open_settings_item(menu, 2, now=0)
        self.assertEqual((menu.section, menu.menu_focus), ('main', 2))


if __name__ == '__main__':
    unittest.main()
