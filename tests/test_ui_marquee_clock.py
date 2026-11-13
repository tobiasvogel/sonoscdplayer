"""Shared marquee clock: every scroll=True text animates without caller timing."""
import unittest
from unittest import mock

from PIL import Image

from sonos_ui import marquee
from sonos_ui.marquee import Marquee, MarqueeClock
from sonos_ui.navigation_pages import NavigationRenderer, SETTINGS_ITEMS
from sonos_ui.render import Renderer

LONG = 'Ein sehr langer Albumtitel, der nicht hineinpasst'


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
        import sonos_ui.render as render_module
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
        from integrations.outputs import oled_status
        t = FakeTime()
        clock = MarqueeClock(clock=t)
        with mock.patch.object(marquee, 'MARQUEE_CLOCK', clock):
            self.assertEqual(oled_status.frame_delay(0.5), 0.5)
            clock.elapsed('x', overflows=True)
            self.assertEqual(oled_status.frame_delay(0.5), oled_status.MARQUEE_FRAME_SECONDS)
            self.assertEqual(oled_status.frame_delay(0.05), 0.05)


if __name__ == '__main__':
    unittest.main()
