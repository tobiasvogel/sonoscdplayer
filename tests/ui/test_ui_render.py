"""UI rendering: Modern Hi-Fi screens, cover policy, display profiles and marquee text."""
import unittest
from unittest import mock
from unittest.mock import patch, Mock
from sonoscdplayer.ui import display_service as oled_status
from sonoscdplayer.hardware.displays.ssd1327 import Framebuffer, SSD1327, DisplayConfig
from sonoscdplayer.ui.displays.layout import DisplayProfile, show_cover
from sonoscdplayer.ui.screens.model import DisplayModel
from sonoscdplayer.ui.renderers.oled_128 import Renderer
from sonoscdplayer.ui.displays.profiles import PROFILES, REFERENCE_PROFILES, profile_for, HardwareDisplayProfile
from PIL import Image
from sonoscdplayer.ui.menu import marquee
from sonoscdplayer.ui.menu.marquee import Marquee, MarqueeClock
from sonoscdplayer.ui.menu.navigation_pages import NavigationRenderer, SETTINGS_ITEMS
LONG = 'Ein sehr langer Albumtitel, der nicht hineinpasst'



def state(mode='PLAYING', track=2, count=11):
    return {'schema_version': 1,
            'player': {'mode': mode, 'track': track, 'track_count': count}}


class ModernOLEDTests(unittest.TestCase):
    def test_modern_frame_is_valid_for_playback(self):
        frame = oled_status.modern_status_frame(state())
        self.assertIsInstance(frame, Framebuffer)
        self.assertEqual(len(frame.data), 128 * 128 // 2)

    def test_modern_frame_supports_idle(self):
        frame = oled_status.modern_status_frame(state('STOPPED', 0, 0))
        self.assertIsInstance(frame, Framebuffer)

    def test_real_no_disc_snapshot_is_renderable(self):
        # PlayerState.NO_DISC reports selected_track=None, track_count=0.
        payload=state('NO_DISC', None, 0)
        frame=oled_status.modern_status_frame(payload)
        self.assertIsInstance(frame, Framebuffer)
        self.assertEqual(len(frame.data), 8192)

    def test_unexpected_mode_renders_modern_error_notice(self):
        payload = state('FUTURE_PLAYER_STATE', None, 0)
        expected = oled_status.notice_frame('status.invalid_player_status',
                                            severity='error', language='de')
        frame = oled_status.safe_modern_status_frame(payload, language='de')
        self.assertEqual(bytes(frame.data), bytes(expected.data))

    def test_invalid_cached_track_information_has_safe_fallback(self):
        payload=state('STOPPED', None, 12)
        frame=oled_status.safe_modern_status_frame(payload, language='en')
        self.assertIsInstance(frame, Framebuffer)

    def test_modern_offline_is_localized_notice(self):
        for language in ('de', 'en'):
            expected = oled_status.notice_frame('status.player_offline',
                                                severity='error', language=language)
            frame = oled_status.modern_status_frame(
                error='socket refused', language=language)
            self.assertEqual(bytes(frame.data), bytes(expected.data))

    def test_bad_schema_is_rejected_without_invented_metadata(self):
        with self.assertRaises(ValueError):
            oled_status.modern_status_frame({'schema_version': 99, 'player': {}})


class CoverAndRecoveryTests(unittest.TestCase):
    def test_small_oled_auto_omits_cover(self):
        self.assertFalse(show_cover('auto', DisplayProfile()))

    def test_large_profile_can_enable_auto_cover(self):
        profile = DisplayProfile(320, 320, supports_cover_auto=True)
        self.assertTrue(show_cover('auto', profile))

    def test_cover_override_on_and_off(self):
        self.assertTrue(show_cover('on'))
        self.assertFalse(show_cover('off', DisplayProfile(320,320,True)))

    def test_cover_mode_rejects_typos(self):
        with self.assertRaises(ValueError):
            show_cover('sometimes')

    def test_render_both_layouts(self):
        model=DisplayModel(view='playback',playback='playing',track=2,track_count=10,
                           title='Ein längerer Titel',album='Albumname')
        for setting in ('auto','on','off'):
            image=Renderer(cover_mode=setting).render(model,grayscale=True)
            self.assertEqual(image.size,(128,128))

    def test_cover_free_layout_differs_from_cover_layout(self):
        model=DisplayModel(view='idle',disc_present=True,album='Album',
                           artist='Interpret',track_count=12)
        no_cover=Renderer(cover_mode='off').render(model)
        with_cover=Renderer(cover_mode='on').render(model)
        self.assertNotEqual(no_cover.tobytes(),with_cover.tobytes())

    def test_adopt_sends_display_on_without_hardware_reset(self):
        gpio=Mock()
        gpio.BCM=11
        gpio.OUT=1
        gpio.LOW=0
        gpio.HIGH=1
        spi=Mock()
        driver=SSD1327(DisplayConfig(),spi=spi,gpio=gpio)
        driver.open(adopt_existing=True)
        self.assertTrue(driver.opened)
        spi.writebytes.assert_called_once_with([0xaf])
        self.assertFalse(any(call.args[0] == driver.config.rst_pin
                             for call in gpio.output.call_args_list))


class DisplayProfileTests(unittest.TestCase):
    def test_three_player_display_candidates(self):
        self.assertEqual(len(PROFILES),3)
        self.assertEqual({p.controller for p in PROFILES.values()},
                         {'SSD1327','ST7735S','SH1107'})

    def test_existing_oled_stays_text_focused(self):
        p=profile_for('ssd1327_128x128')
        self.assertFalse(p.cover_enabled())
        self.assertTrue(p.driver_ready)

    def test_tft_proposes_cover(self):
        p=profile_for('st7735s_128x160')
        self.assertTrue(p.cover_enabled())
        self.assertFalse(p.driver_ready)

    def test_narrow_monochrome_omits_cover(self):
        p=profile_for('sh1107_64x128')
        self.assertFalse(p.cover_enabled())

    def test_reference_eink_refresh_only_on_events(self):
        p=REFERENCE_PROFILES['epaper_200x200_4color']
        self.assertEqual(p.refresh_policy,'event_driven')
        self.assertEqual(p.controller,'UNKNOWN')
        self.assertFalse(p.driver_ready)

    def test_user_override_always_applies(self):
        for p in PROFILES.values():
            self.assertTrue(p.cover_enabled('on'))
            self.assertFalse(p.cover_enabled('off'))

    def test_invalid_profile_fails(self):
        with self.assertRaises(ValueError):
            HardwareDisplayProfile('bad','X',0,128,'mono1',False,'interactive')


class FakeTime:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class MarqueeClockTests(unittest.TestCase):
    def test_elapsed_starts_per_text_and_restarts_after_absence(self):
        t = FakeTime()
        clock = MarqueeClock(clock=t, forget_after=1.0)
        self.assertEqual(clock.elapsed('a', overflows=True), 0)
        t.now += 0.5
        self.assertEqual(clock.elapsed('a', overflows=True), 0.5)
        self.assertEqual(clock.elapsed('b', overflows=False), 0, 'new text starts fresh')
        t.now += 2.0                                   # 'a' not drawn for 2 s
        self.assertEqual(clock.elapsed('a', overflows=True), 0, 'restarts with pause')

    def test_animating_only_while_something_overflows(self):
        t = FakeTime()
        clock = MarqueeClock(clock=t)
        self.assertFalse(clock.animating())
        clock.elapsed('short', overflows=False)
        self.assertFalse(clock.animating())
        clock.elapsed('long', overflows=True)
        self.assertTrue(clock.animating())
        t.now += 1.5
        self.assertFalse(clock.animating())

    def test_cache_is_bounded(self):
        t = FakeTime()
        clock = MarqueeClock(clock=t)
        for i in range(300):
            t.now += 0.01
            clock.elapsed(i, overflows=False)
        self.assertLessEqual(len(clock._started), 257)


class RendererAutoScrollTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeTime()
        patcher = mock.patch.object(marquee, 'MARQUEE_CLOCK', MarqueeClock(clock=self.t))
        patcher.start()
        self.addCleanup(patcher.stop)
        from sonoscdplayer.ui.renderers import oled_128 as render_module
        patcher2 = mock.patch.object(render_module, 'MARQUEE_CLOCK', marquee.MARQUEE_CLOCK)
        patcher2.start()
        self.addCleanup(patcher2.stop)

    def draw(self, value, *, scroll=True, time=None):
        r = Renderer()
        im = Image.new('RGB', (128, 128), 'black')
        r.text(im, (6, 18), value, fill='white', size=12, width=60, height=17,
               scroll=scroll, time=time)
        return im.tobytes()

    def run_frames(self, seconds, value, **kwargs):
        """Advance like the display loop: one frame every 0.1 s."""
        frame = None
        for _ in range(round(seconds / 0.1)):
            self.t.now += 0.1
            frame = self.draw(value, **kwargs)
        return frame

    def test_long_text_moves_after_the_initial_pause(self):
        first = self.draw(LONG)
        paused = self.run_frames(Marquee().initial_pause - 0.2, LONG)
        self.assertTrue(paused == first, 'initial pause')
        moved = self.run_frames(1.0, LONG)
        self.assertTrue(moved != first, 'scrolling')

    def test_short_or_non_scrolling_text_never_moves(self):
        short = self.draw('Kurz')
        fixed = self.draw(LONG, scroll=False)
        self.assertTrue(self.run_frames(5, 'Kurz') == short)
        self.assertTrue(self.run_frames(5, LONG, scroll=False) == fixed)

    def test_explicit_time_stays_deterministic(self):
        self.assertTrue(self.draw(LONG, time=0) == self.draw(LONG, time=0))
        self.assertTrue(self.draw(LONG, time=0) != self.draw(LONG, time=5))

    def test_focused_menu_entry_scrolls(self):
        nav = NavigationRenderer()
        index = SETTINGS_ITEMS.index('menu.rescan')
        first = nav.page('menu.settings', SETTINGS_ITEMS, index, language='de').tobytes()
        for _ in range(30):
            self.t.now += 0.1
            moved = nav.page('menu.settings', SETTINGS_ITEMS, index, language='de').tobytes()
        self.assertTrue(first != moved, '"Lautsprecher suchen" is too wide and scrolls')
        self.assertTrue(marquee.MARQUEE_CLOCK.animating())


class FrameDelayTests(unittest.TestCase):
    def test_faster_redraw_only_while_animating(self):
        from sonoscdplayer.ui import display_service as oled_status
        t = FakeTime()
        clock = MarqueeClock(clock=t)
        with mock.patch.object(marquee, 'MARQUEE_CLOCK', clock):
            self.assertEqual(oled_status.frame_delay(0.5), 0.5)
            clock.elapsed('x', overflows=True)
            self.assertEqual(oled_status.frame_delay(0.5), oled_status.MARQUEE_FRAME_SECONDS)
            self.assertEqual(oled_status.frame_delay(0.05), 0.05)



class MenuRowSizeTests(unittest.TestCase):
    """All encoder menus use the same rows as the power menu (11 px text)."""

    def highlight_rows(self, image):
        gray = image.convert('L')
        rows = []
        for y in range(24, 100):  # below the heading and the speaker count line
            value = gray.getpixel((7, y))  # inside the highlight, left of any marker
            if value > 40:
                rows.append(y)
        return (min(rows), max(rows)) if rows else None

    def test_power_main_and_speaker_menus_share_geometry(self):
        from types import SimpleNamespace as NS
        from sonoscdplayer.ui.renderers.oled_128_speakers import MultiRenderer
        from sonoscdplayer.ui.menu.navigation_pages import NavigationRenderer, MAIN_ITEMS
        from sonoscdplayer.ui.menu.speakers.multi_target import MultiSelection, from_devices
        from sonoscdplayer.ui.renderers.oled_128 import MENU_ROW_TOP, MENU_ROW_HEIGHT
        device = NS(uid='A', player_name='Hobbyraum')
        device.group = NS(coordinator=device, members=[device])
        renderer = MultiRenderer(language='de')
        images = {
            'power': renderer.render_power(0),
            'main': NavigationRenderer().page('menu.main', MAIN_ITEMS, 0, language='de'),
            'targets': renderer.render_multi(MultiSelection().update(from_devices([device]))),
        }
        for name, image in images.items():
            with self.subTest(menu=name):
                self.assertEqual(self.highlight_rows(image),
                                 (MENU_ROW_TOP, MENU_ROW_TOP + MENU_ROW_HEIGHT))

    def test_three_rows_visible_and_focus_always_shown(self):
        from sonoscdplayer.ui.renderers.oled_128 import Renderer
        r = Renderer()
        self.assertEqual(list(r.menu_window(4, 0)), [0, 1, 2])
        self.assertEqual(list(r.menu_window(4, 3)), [1, 2, 3])
        self.assertEqual(list(r.menu_window(2, 1)), [0, 1])

if __name__ == '__main__':
    unittest.main()
