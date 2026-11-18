"""ARC-DISPLAY-01D: the OLED is drawn from InteractiveMenu.semantic_screen().

The reference below is the OLED composition as it was before 01D (built
from the unchanged drawing primitives). Every state must still give the
very same pixels.
"""
import unittest
from dataclasses import replace
from unittest.mock import patch

from PIL import Image

from network_setup.session import SetupSnapshot
from sonos_ui.graphic_views import graphic_frame_from_menu, render_screen
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.menu_catalog import NAVIGATION_PAGES
from sonos_ui.multi_target import Target, Topology
from sonos_ui.network_status import NetworkStatus
from sonos_ui.notice import Notice
from sonos_ui.notice_policy import NoticeTiming
from sonos_ui.theme import ssd1327_gray


def reference_frame(menu, now):
    """Pre-01D InteractiveMenu.frame(), composed from drawing primitives."""
    r = menu.renderer
    t = r.theme
    tr = r.tr
    notice = (menu.connect_notices.current(now=now)
              if menu._notice_previous_active is not None else None)
    if notice is not None:
        return menu._connect_renderer.render(
            notice, language=menu.language, grayscale=True,
            dismiss_hint=NoticeTiming().encoder_can_confirm(notice))
    setup = menu.netsetup_snapshot()
    if setup is not None and not menu.power_menu:
        return menu.wifi_entry.frame(r, setup, now=now)
    if menu.power_menu:
        im = Image.new('RGB', (128, 128), t.background)
        r.shell(im, tr('menu.power'))
        for pos, (key, mark) in enumerate((('menu.back', '<'), ('menu.poweroff', '×'),
                                           ('menu.reboot', '↻'))):
            r.menu_row(im, pos, tr(key), selected=pos == menu.power_focus, marker=mark)
        r.bottom(im, status=tr(menu.power_notice or 'status.power_choose'),
                 footer=tr('footer.select'))
        return ssd1327_gray(im)
    if menu.volume_overlay_active(now=now):
        return r.render_volume(menu._volume_level, gray=True)
    if menu.language_picker:
        im = Image.new('RGB', (128, 128), t.background)
        r.shell(im, tr('menu.language'))
        for slot, index in enumerate(r.menu_window(len(menu.languages), menu.language_focus)):
            r.menu_row(im, slot, r.translator.language_name(menu.languages[index]),
                       selected=index == menu.language_focus)
        r.bottom(im, status=tr('status.local_only'), footer=tr('footer.confirm'))
        return im
    nav = menu._navigation_renderer
    if menu.active and menu.section in NAVIGATION_PAGES:
        heading, items = NAVIGATION_PAGES[menu.section]
        im = Image.new('RGB', (128, 128), t.background)
        nav.shell(im, tr(heading))
        for slot, index in enumerate(nav.menu_window(len(items), menu.menu_focus)):
            nav.menu_row(im, slot, tr(items[index]), selected=index == menu.menu_focus,
                         marker='<' if items[index] == 'menu.back' else '>')
        nav.bottom(im, status=tr('status.local_only'), footer=tr('footer.select'))
        return ssd1327_gray(im)
    if menu.active and menu.section == 'network_status':
        s = menu.network_snapshot
        im = Image.new('RGB', (128, 128), t.background)
        nav.shell(im, tr('network.status'))
        for index, (label, value) in enumerate((
                ('network.ethernet', 'network.state.' + s.ethernet),
                ('network.wifi', 'network.state.' + s.wifi),
                ('network.preferred', 'network.preferred.' + s.preferred))):
            y = 23 + index * 25
            nav.text(im, (6, y), tr(label), fill=t.secondary, size=9, width=116, height=11)
            nav.text(im, (9, y + 11), tr(value), fill=t.primary, size=10, bold=True,
                     width=113, height=13)
        nav.bottom(im, status=tr('network.readonly'), footer=tr('footer.select'))
        return ssd1327_gray(im)
    state = menu.state
    count = len(state.topology.targets)
    im = Image.new('RGB', (128, 128), t.background)
    r.shell(im, tr('menu.targets'))
    r.text(im, (6, 16), tr('status.targets', selected=len(state.draft), total=count),
           fill=t.secondary, size=8, width=116, height=10)
    if not count:
        key = 'status.scan_running' if menu.searching() else 'status.no_targets'
        r.text(im, (7, 74), tr(key), fill=t.secondary, size=9, width=115, height=15)
    for slot, pos in enumerate(r.menu_window(count + 2, state.focus)):
        if pos == count:
            name, mark = tr('menu.review'), '>'
        elif pos == count + 1:
            name, mark = tr('menu.back'), '<'
        else:
            target = state.topology.targets[pos]
            name, mark = target.label, '✓' if target.key in state.draft else '□'
        r.menu_row(im, slot, name, selected=pos == state.focus, marker=mark)
    status = tr(state.notice) if state.notice else (
        tr('status.incomplete') if state.topology.error else tr('status.no_changes'))
    r.bottom(im, status=status, footer=tr('footer.mark'))
    return ssd1327_gray(im)


def speakers(n):
    return Topology(tuple(Target(f"uid-{i}", f"Lautsprecher {i}", (f"uid-{i}",),
                                 (f"Lautsprecher {i}",)) for i in range(n)))


class SetupStub:
    def __init__(self, snapshot):
        self.current = snapshot

    def snapshot(self):
        return self.current


class OledSemanticParityTests(unittest.TestCase):
    def assert_parity(self, menu, now=1):
        with patch("sonos_ui.render.MARQUEE_CLOCK.elapsed", return_value=0), \
             patch("sonos_ui.interactive_menu.time.monotonic", return_value=now):
            new = menu.frame(lambda image: image)
            old = reference_frame(menu, now)
        self.assertEqual((new.mode, new.tobytes()), (old.mode, old.tobytes()))

    def test_navigation_pages_and_network_status(self):
        for language in ("de", "en"):
            menu = InteractiveMenu(language=language)
            menu.active = True
            for section, (_, keys) in NAVIGATION_PAGES.items():
                for focus in range(len(keys)):
                    with self.subTest(language=language, section=section, focus=focus):
                        menu.section, menu.menu_focus = section, focus
                        self.assert_parity(menu)
            menu.section = "network_status"
            for snapshot in (NetworkStatus(),
                             NetworkStatus(ethernet="connected", wifi="disconnected",
                                           preferred="ethernet")):
                with self.subTest(language=language, snapshot=snapshot):
                    menu.network_snapshot = snapshot
                    self.assert_parity(menu)

    def test_speaker_list_including_feedback_and_empty_list(self):
        for language in ("de", "en"):
            menu = InteractiveMenu(language=language)
            menu.active, menu.section = True, "targets"
            for n in (0, 1, 4):
                topology = speakers(n)
                for focus in range(n + 2):
                    for draft in (frozenset(), frozenset(t.key for t in topology.targets[:1])):
                        for notice, pending in (("", False), ("status.target_rejected", False),
                                                ("", True), ("menu.back", False)):
                            with self.subTest(language=language, n=n, focus=focus,
                                              draft=len(draft), notice=notice, pending=pending):
                                menu.state = replace(menu.state, topology=topology,
                                                     focus=focus, draft=draft, notice=notice)
                                menu._target_pending = pending
                                self.assert_parity(menu)
            menu._target_pending = False
            menu.state = replace(menu.state, notice="", focus=0, topology=Topology((), error="x"))
            self.assert_parity(menu)
            menu.active = False                      # inactive: speaker list as before
            self.assert_parity(menu)

    def test_power_volume_language_and_notices(self):
        for language in ("de", "en"):
            menu = InteractiveMenu(language=language, power_action=lambda _a: True)
            menu.active, menu.section = True, "main"
            menu.power_menu = True
            for focus in range(3):
                for notice in ("", "status.power_failed"):
                    with self.subTest(language=language, focus=focus, notice=notice):
                        menu.power_focus, menu.power_notice = focus, notice
                        self.assert_parity(menu)
            menu.power_menu, menu.power_notice = False, ""
            for level in (0, 42, 100):
                menu._volume_level, menu._volume_until = level, 100
                self.assert_parity(menu)
            menu._volume_until = 0
            menu.language_picker = True
            for focus in range(len(menu.languages)):
                menu.language_focus = focus
                self.assert_parity(menu)
            menu.language_picker = False
            for duration in (0, 5):
                fresh = InteractiveMenu(language=language)
                fresh.active, fresh.section = True, "main"
                fresh._notice_previous_active = False
                fresh.connect_notices.post(Notice("status.player_offline", severity="error",
                                                  duration_seconds=duration), now=0)
                with self.subTest(language=language, duration=duration):
                    self.assert_parity(fresh, now=0)

    def test_wlan_setup_still_uses_its_own_renderer(self):
        menu = InteractiveMenu(network_setup=SetupStub(SetupSnapshot(
            state="hotspot", hotspot_ssid="SonosCDPlayer-Test", password="ABCDEFGH",
            url="http://192.168.4.1", remaining=600)))
        menu.active, menu.section = True, "main"
        self.assertIsNone(menu.semantic_screen(now=1))
        self.assert_parity(menu)
        menu.power_menu = True                       # POWER covers the setup
        self.assertEqual(menu.semantic_screen(now=1).view, "power")
        self.assert_parity(menu)

    def test_shutdown_screens_stay_with_the_system_screen(self):
        menu = InteractiveMenu()
        menu.shutdown_screen = "reboot"
        self.assertEqual(menu.semantic_screen(now=0).view, "system")
        with self.assertRaises(ValueError):
            graphic_frame_from_menu(menu, now=0)     # drawn by system_screen instead
        self.assertEqual(menu.frame(lambda image: image).size, (128, 128))

    def test_every_semantic_view_has_an_oled_widget(self):
        menu = InteractiveMenu()
        menu.active, menu.section = True, "main"
        for screen in (menu.semantic_screen(now=0),):
            self.assertEqual(render_screen(screen, renderer=menu.renderer).size, (128, 128))
        from sonos_ui.semantic_screen import SemanticScreen
        with self.assertRaises(ValueError):
            render_screen(SemanticScreen("idle", view="unknown"), renderer=menu.renderer)


if __name__ == "__main__":
    unittest.main()
