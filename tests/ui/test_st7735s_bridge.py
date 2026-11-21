"""Offline tests for the ST7735S runtime rendering seam (no hardware access)."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sonoscdplayer.hardware.displays.st7735s import DisplayConfig
from sonoscdplayer.ui.displays.st7735s_bridge import ST7735SFrameBridge
from sonoscdplayer.ui.screens.semantic_screen import ScreenItem, SemanticScreen

ROOT = Path(__file__).resolve().parents[2]


class ST7735SBridgeTests(unittest.TestCase):
    def test_semantic_menu_and_notice_are_rgb565_landscape_frames(self):
        bridge = ST7735SFrameBridge()
        screens = (
            SemanticScreen('menu', heading='Menu', items=(
                ScreenItem('a', 'First', focused=True), ScreenItem('b', 'Second'))),
            SemanticScreen('notice', message='Offline', severity='info'),
        )
        for screen in screens:
            with self.subTest(screen=screen.kind):
                frame = bridge.from_screen(screen)
                self.assertEqual((frame.width, frame.height, frame.pixel_format, len(frame.data)),
                                 (160, 128, 'rgb565', 40960))

    def test_rejects_portrait_and_bad_screen(self):
        with self.assertRaisesRegex(ValueError, 'landscape'):
            ST7735SFrameBridge(config=DisplayConfig(rotation=0))
        with self.assertRaises(TypeError):
            ST7735SFrameBridge().from_screen({'kind': 'menu'})

    def test_player_schema_is_validated_before_rendering(self):
        with self.assertRaisesRegex(ValueError, 'schema'):
            ST7735SFrameBridge().from_player_status({'schema_version': 99})
        status = {'schema_version': 1, 'player': {
            'mode': 'NO_DISC', 'track_count': 0, 'track': None}}
        frame = ST7735SFrameBridge().from_player_status(status)
        self.assertEqual(len(frame.data), 40960)

    def test_menu_is_read_only_and_external_owner_is_not_overridden(self):
        class StubMenu:
            calls = 0
            def semantic_screen(self, *, now):
                self.calls += 1
                return None
        menu = StubMenu()
        self.assertIsNone(ST7735SFrameBridge().from_menu(menu, now=1))
        self.assertEqual(menu.calls, 1)

    def test_frame_conversion_does_not_open_hardware(self):
        with patch('sonoscdplayer.hardware.displays.st7735s.ST7735S.open',
                   side_effect=AssertionError('hardware access forbidden')):
            ST7735SFrameBridge().from_screen(SemanticScreen('idle'))

    def test_preview_cli_writes_three_images_only_to_requested_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'preview'
            result = subprocess.run([sys.executable, str(ROOT / 'tools/st7735s_bridge_preview.py'),
                                     '--output-dir', str(dest)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Offline preview only', result.stdout)
            self.assertEqual(sorted(x.name for x in dest.iterdir()),
                             ['st7735s-menu.png', 'st7735s-notice.png', 'st7735s-playback.png'])


if __name__ == '__main__':
    unittest.main()
