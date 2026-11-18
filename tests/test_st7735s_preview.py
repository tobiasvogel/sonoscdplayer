"""160x128 renderer: bounded text, focus, colours and progress."""
import unittest
from dataclasses import replace

from PIL import Image, ImageColor
from sonos_ui.landscape_graphic import LandscapeRenderer, menu_window
from sonos_ui.semantic_screen import SemanticScreen
from sonos_ui.theme import MODERN_HIFI
from integrations.outputs.st7735s import DisplayConfig, image_to_frame
from display_samples import all_screens, menu_screen, overview_screen


class LandscapeRendererTests(unittest.TestCase):
    def setUp(self):
        self.renderer = LandscapeRenderer()

    def test_all_sample_scenes_pack_as_160x128_rgb565(self):
        for name, screen in all_screens():
            with self.subTest(scene=name):
                image = self.renderer.render(screen)
                self.assertEqual((image.mode, image.size), ('RGB', (160, 128)))
                frame = image_to_frame(image, DisplayConfig(rotation=270))
                self.assertEqual((frame.width, frame.height, len(frame.data)), (160, 128, 40960))

    def test_title_scroll_does_not_change_other_fields_or_progress(self):
        screen = overview_screen(0)
        first = self.renderer.render(screen, elapsed=0)
        later = self.renderer.render(screen, elapsed=3)
        self.assertNotEqual(first.crop((4, 41, 156, 59)).tobytes(), later.crop((4, 41, 156, 59)).tobytes())
        self.assertEqual(first.crop((0, 0, 160, 41)).tobytes(), later.crop((0, 0, 160, 41)).tobytes())
        self.assertEqual(first.crop((0, 59, 160, 128)).tobytes(), later.crop((0, 59, 160, 128)).tobytes())

    def test_focused_room_scroll_stays_inside_its_row_and_before_checkbox(self):
        screen = menu_screen(2)
        first = self.renderer.render(screen, elapsed=0)
        later = self.renderer.render(screen, elapsed=3)
        self.assertNotEqual(first.crop((20, 57, 138, 77)).tobytes(), later.crop((20, 57, 138, 77)).tobytes())
        self.assertEqual(first.crop((0, 0, 160, 57)).tobytes(), later.crop((0, 0, 160, 57)).tobytes())
        self.assertEqual(first.crop((138, 34, 160, 100)).tobytes(), later.crop((138, 34, 160, 100)).tobytes())

    def test_menu_window_keeps_every_focus_visible_and_selection_stable(self):
        for focus in range(6):
            screen = menu_screen(focus)
            self.assertIn(focus, menu_window(screen))
            self.assertEqual(len(menu_window(screen)), 3)
            self.assertEqual([i for i, item in enumerate(screen.items) if item.selected], [0])
            self.renderer.render(screen)

    def test_focus_color_and_selected_marker_are_independent(self):
        image = self.renderer.render(menu_screen(1))
        self.assertEqual(image.getpixel((157, 57)), ImageColor.getrgb(MODERN_HIFI.accent))
        self.assertEqual(image.getpixel((147, 43)), ImageColor.getrgb(MODERN_HIFI.primary))

    def test_progress_zero_half_clamped_and_unknown(self):
        def progress(current, total):
            screen = overview_screen()
            items = tuple(replace(item, value=str(current if item.key == 'elapsed_seconds' else total))
                          if item.key in ('elapsed_seconds', 'duration_seconds') else item
                          for item in screen.items)
            image = self.renderer.render(replace(screen, items=items))
            return [image.getpixel((x, 107)) for x in range(4, 156)]
        accent = ImageColor.getrgb(MODERN_HIFI.accent)
        self.assertEqual(progress(0, 100).count(accent), 0)
        self.assertEqual(progress(50, 100).count(accent), 76)
        self.assertEqual(progress(200, 100).count(accent), 152)
        self.assertEqual(progress(5, 0).count(accent), 0)
        image = self.renderer.render(SemanticScreen('idle'))
        self.assertEqual(image.getpixel((10, 107)), ImageColor.getrgb(MODERN_HIFI.background))

    def test_text_cannot_paint_outside_viewport(self):
        image = Image.new('RGB', (160, 128), MODERN_HIFI.background)
        before = image.copy()
        self.renderer._text(image, (20, 40, 50, 15), 'Küche Gästezimmer ' * 10)
        from PIL import ImageChops
        left, top, right, bottom = ImageChops.difference(image, before).getbbox()
        self.assertTrue(20 <= left < right <= 70)
        self.assertTrue(40 <= top < bottom <= 55)
        with self.assertRaises(ValueError):
            self.renderer._text(image, (155, 120, 10, 10), 'X')

    def test_missing_focus_and_invalid_animation_fail(self):
        screen = menu_screen(0)
        with self.assertRaises(ValueError):
            self.renderer.render(replace(screen, items=tuple(replace(item, focused=False) for item in screen.items)))
        for elapsed in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.renderer.render(screen, elapsed=elapsed)


if __name__ == '__main__':
    unittest.main()
