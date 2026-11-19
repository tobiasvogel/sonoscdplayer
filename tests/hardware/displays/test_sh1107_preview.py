"""Compact 128x64 renderer: viewport bounds, focus, scrolling and timing."""
import unittest

from PIL import Image
from sonoscdplayer.ui.renderers.compact_128x64 import CompactRenderer, compact_screen_from_model, menu_window
from sonoscdplayer.ui.screens.model import DisplayModel
from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen, ScreenItem
from sonoscdplayer.hardware.displays.sh1107 import DisplayConfig, image_to_frame


from display_samples import all_screens, menu_screen, overview_screen


def lit_pixels(image):
    """Number of pixels that are not black."""
    histogram = image.histogram()
    return sum(histogram) - histogram[0]


class CompactRendererTests(unittest.TestCase):
    def setUp(self):
        self.renderer = CompactRenderer()

    def test_all_scenes_are_monochrome_and_pack_to_1024_bytes(self):
        for name, screen in all_screens():
            with self.subTest(scene=name):
                image = self.renderer.render(screen)
                self.assertEqual((image.mode, image.size), ('1', (128, 64)))
                self.assertEqual(len(image_to_frame(image, DisplayConfig(rotation=270)).data), 1024)

    def test_scrolling_moves_long_title_and_resets_at_zero(self):
        screen = overview_screen()
        initial = self.renderer.render(screen, elapsed=0)
        later = self.renderer.render(screen, elapsed=3)
        self.assertNotEqual(initial.crop((2, 13, 126, 25)).tobytes(),
                            later.crop((2, 13, 126, 25)).tobytes())
        self.assertEqual(initial.tobytes(), self.renderer.render(screen, elapsed=0).tobytes())
        # The same screen's clock and progress must not move merely from animation.
        self.assertEqual(initial.crop((0, 49, 128, 64)).tobytes(),
                         later.crop((0, 49, 128, 64)).tobytes())

    def test_focused_long_label_scrolls_without_touching_adjacent_rows(self):
        screen = menu_screen(2)
        initial = self.renderer.render(screen, elapsed=0)
        later = self.renderer.render(screen, elapsed=3)
        self.assertNotEqual(initial.crop((12, 26, 115, 38)).tobytes(),
                            later.crop((12, 26, 115, 38)).tobytes())
        self.assertEqual(initial.crop((0, 0, 128, 26)).tobytes(),
                         later.crop((0, 0, 128, 26)).tobytes())
        self.assertEqual(initial.crop((115, 14, 128, 50)).tobytes(),
                         later.crop((115, 14, 128, 50)).tobytes())

    def test_every_menu_focus_is_visible_without_changing_selection(self):
        windows = ((0, 1, 2), (0, 1, 2), (1, 2, 3), (2, 3, 4), (3, 4, 5), (3, 4, 5))
        for focus, expected in enumerate(windows):
            screen = menu_screen(focus)
            self.assertEqual(tuple(menu_window(screen)), expected)
            self.assertIn(focus, menu_window(screen))
            self.assertEqual([i for i, item in enumerate(screen.items) if item.selected], [0])
            self.renderer.render(screen)

    def test_focus_and_selection_have_distinct_markers(self):
        image = self.renderer.render(menu_screen(1))
        self.assertEqual(image.getpixel((122, 20)), 1)  # selected row 0
        self.assertEqual(image.getpixel((127, 26)), 1)  # focus highlight, row 1
        self.assertEqual(image.getpixel((119, 29)), 0)  # unselected box border on focus

    def test_empty_menu_and_missing_focus(self):
        self.renderer.render(SemanticScreen('menu', heading='Leer', placeholder='Keine Räume'))
        with self.assertRaises(ValueError):
            self.renderer.render(SemanticScreen('menu', items=(ScreenItem('a', 'A'),)))

    def test_times_clamp_progress_and_handle_zero_duration(self):
        def bar(current, total):
            model = DisplayModel(view='playback', elapsed_seconds=current, duration_seconds=total)
            return self.renderer.render(compact_screen_from_model(model)).crop((2, 62, 126, 63))
        self.assertIsNone(bar(0, 100).getbbox())
        self.assertIsNone(bar(5, 0).getbbox())
        self.assertEqual(lit_pixels(bar(50, 100)), 62)
        self.assertEqual(lit_pixels(bar(200, 100)), 124)

    def test_optional_times_and_absent_disc_do_not_invent_progress(self):
        empty = compact_screen_from_model(DisplayModel(disc_present=False, title='stale',
                                          elapsed_seconds=10, duration_seconds=100))
        keys = {item.key for item in empty.items}
        self.assertNotIn('track_title', keys)
        self.assertNotIn('elapsed_seconds', keys)
        image = self.renderer.render(empty)
        self.assertIsNone(image.crop((0, 61, 128, 64)).getbbox())
        for value in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                compact_screen_from_model(DisplayModel(elapsed_seconds=value))

    def test_text_is_clipped_to_its_viewport(self):
        canvas = Image.new('1', (128, 64))
        self.renderer._text(canvas, (10, 20, 30, 10), 'ÄÖÜ Küche ' * 10)
        left, top, right, bottom = canvas.getbbox()
        self.assertTrue(10 <= left < right <= 40)
        self.assertTrue(20 <= top < bottom <= 30)
        with self.assertRaises(ValueError):
            self.renderer._text(canvas, (120, 60, 10, 10), 'X')

    def test_invalid_animation_times_fail(self):
        for value in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.renderer.render(overview_screen(), elapsed=value)


if __name__ == '__main__':
    unittest.main()
