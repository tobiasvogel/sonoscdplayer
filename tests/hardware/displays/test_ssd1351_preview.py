"""Production RGB widgets through the SSD1351 offline/hardware demo path."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageColor
from sonoscdplayer.hardware.displays.ssd1351 import image_to_frame
from sonoscdplayer.hardware.displays.ssd1351_preview import (
    demo_images, menu_screens, overview_models,
)
from sonoscdplayer.hardware.displays.ssd1351_test import main
from sonoscdplayer.ui.renderers import oled_128
from sonoscdplayer.ui.renderers.theme import MODERN_HIFI


class PreviewTests(unittest.TestCase):
    def setUp(self):
        # Optional location for the same DejaVu fonts on development hosts.
        if os.environ.get('SONOSCDPLAYER_TEST_FONT_DIR'):
            font_patch = patch.object(oled_128, 'FONT_DIR',
                                     Path(os.environ['SONOSCDPLAYER_TEST_FONT_DIR']))
            font_patch.start()
            self.addCleanup(font_patch.stop)
        clock = patch.object(oled_128.MARQUEE_CLOCK, 'elapsed', return_value=0)
        clock.start()
        self.addCleanup(clock.stop)

    def test_all_scenes_keep_rgb_palette_and_frame_contract(self):
        images = demo_images('all')
        self.assertEqual(len(images), 10)
        for name, image in images:
            with self.subTest(scene=name):
                self.assertEqual((image.mode, image.size), ('RGB', (128, 128)))
                frame = image_to_frame(image)
                self.assertEqual((frame.width, frame.height, frame.pixel_format,
                                  len(frame.data)), (128, 128, 'rgb565', 32768))
                colors = [color for _, color in image.getcolors(maxcolors=128*128)]
                self.assertIn(ImageColor.getrgb(MODERN_HIFI.accent), colors)
                self.assertTrue(any(r != g or g != b for r, g, b in colors))

    def test_overview_uses_production_widget_without_grayscale(self):
        original = oled_128.Renderer.render
        calls = []
        def render(renderer, model, **kwargs):
            calls.append((model, kwargs))
            return original(renderer, model, **kwargs)
        with patch.object(oled_128.Renderer, 'render', render):
            demo_images('overview')
        self.assertEqual([m for m, _ in calls], [m for _, m in overview_models()])
        self.assertTrue(all(kwargs == dict(elapsed=0, grayscale=False)
                            for _, kwargs in calls))

    def test_menu_focus_selection_and_scrolling_window_are_independent(self):
        screens = [screen for _, screen in menu_screens()]
        renderer = oled_128.Renderer()
        self.assertEqual(len(screens), 7)
        for focus, screen in enumerate(screens[:6]):
            self.assertEqual([i for i, item in enumerate(screen.items) if item.focused], [focus])
            self.assertEqual([i for i, item in enumerate(screen.items) if item.selected], [0])
            self.assertIn(focus, renderer.menu_window(len(screen.items), focus))
        self.assertEqual([i for i, item in enumerate(screens[-1].items) if item.selected], [0, 1])
        self.assertEqual([item.intent for item in screens[0].items[-2:]], ['review_target', 'back'])

    def test_offline_demo_writes_pngs_without_hardware_or_network(self):
        import socket
        with tempfile.TemporaryDirectory() as directory, \
             patch('sonoscdplayer.hardware.displays.ssd1351_test.SPITransport') as transport, \
             patch.object(socket, 'socket', side_effect=AssertionError('network access')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            main(['--demo', 'all', '--preview-dir', directory])
            files = list(Path(directory).glob('*.png'))
            self.assertEqual(len(files), 10)
            for file in files:
                with Image.open(file) as image:
                    self.assertEqual((image.mode, image.size), ('RGB', (128, 128)))
        transport.assert_not_called()
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(reports), 10)
        self.assertTrue(all(not r['hardware'] and r['bytes'] == 32768 for r in reports))

    def test_hardware_menu_sends_each_scene_with_requested_config(self):
        driver = Mock()
        with patch('sonoscdplayer.hardware.displays.ssd1351_test.SSD1351', return_value=driver) as factory, \
             patch('sonoscdplayer.hardware.displays.ssd1351_test.time.sleep') as sleep, \
             contextlib.redirect_stdout(io.StringIO()):
            main(['--demo', 'menu', '--gpio', '--execute', '--dc-pin', '22',
                  '--rst-pin', '27', '--rotation', '270', '--brightness', '5', '--seconds', '2'])
        self.assertEqual(factory.call_args.args[1].rotation, 270)
        driver.open.assert_called_once()
        self.assertEqual(driver.show.call_count, 7)
        self.assertEqual([call.args[0] for call in driver.set_brightness.call_args_list], [5]*7)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2]*7)
        self.assertGreater(len({call.args[0].data for call in driver.show.call_args_list}), 1)
        driver.close.assert_called_once()

    def test_missing_font_fails_before_transport_is_created(self):
        with patch.object(oled_128, 'FONT_DIR', Path('/missing/ssd1351/fonts')), \
             patch('sonoscdplayer.hardware.displays.ssd1351_test.SPITransport') as transport:
            with self.assertRaisesRegex(RuntimeError, 'fonts-dejavu-core'):
                main(['--demo', 'overview', '--gpio', '--execute'])
        transport.assert_not_called()

    def test_demo_and_pattern_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), \
             patch('sonoscdplayer.hardware.displays.ssd1351_test.SPITransport') as transport:
            with self.assertRaises(SystemExit):
                main(['--demo', 'menu', '--pattern', 'colors', '--gpio', '--execute'])
        transport.assert_not_called()

    def test_invalid_demo_is_rejected(self):
        with self.assertRaises(ValueError):
            demo_images('unknown')


if __name__ == '__main__':
    unittest.main()
