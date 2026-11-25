"""Original logo geometry and supplied paint survive every display output path."""
from functools import partial
from pathlib import Path
import re
import tempfile
import unittest
from xml.etree import ElementTree

from PIL import Image
from sonoscdplayer.hardware.displays.frames import image_to_frame
from sonoscdplayer.hardware.displays.pins import DisplayPins
from sonoscdplayer.ui.display_service import start_splash, DEFAULT_LOGO
from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
from sonoscdplayer.ui import system_screens
from tools.build_display_splash import build
from tools.build_shutdown_renderer import generate

ROOT = Path(__file__).resolve().parents[2]
ARTWORK = ROOT/'src/sonoscdplayer/assets/screens'
GOLD = (245, 169, 74)
CREAM = (238, 233, 225)
INK = (26, 24, 22)


def palette(image):
    return {color for _, color in image.getcolors(image.width*image.height)}


def rgb565(rgb):
    r, g, b = rgb
    return (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)).to_bytes(2, 'big')


def frame_palette(frame):
    return {frame.data[i:i+2] for i in range(0, len(frame.data), 2)}


class ColorSystemLogoTests(unittest.TestCase):
    def test_colored_svg_preserves_all_original_geometry(self):
        def geometry(path):
            result = []
            for node in ElementTree.parse(path).getroot().iter():
                tag = node.tag.rsplit('}', 1)[-1]
                if tag == 'path': result.append(('path', node.attrib['d']))
                elif tag == 'circle':
                    result.append(('circle', tuple(node.attrib[key] for key in ('cx','cy','r'))))
            return result
        original = geometry(ROOT/'docs/logo_new.svg')
        self.assertEqual(len(original), 9)
        self.assertEqual(geometry(ARTWORK/'logo.svg'), original)

    def test_all_logo_artwork_has_supplied_colors_on_black(self):
        for name in ('start_screen.png', 'shutdown_screen.png', 'reboot_screen.png',
                     'restart_screen.png', 'pleasewait_screen.png'):
            with self.subTest(name=name), Image.open(ARTWORK/name) as image:
                self.assertEqual((image.mode, image.size), ('RGB', (128,128)))
                self.assertTrue({GOLD, CREAM, INK}.issubset(palette(image)))
                self.assertTrue(all(image.getpixel((0,y)) == (0,0,0) for y in range(128)))
        # The safe-screen power symbol is not a logo and remains monochrome.
        with Image.open(ARTWORK/'safetounplug_screen.png') as image:
            self.assertTrue(all(r == g == b for r,g,b in palette(image.convert('RGB'))))

    def test_localized_system_images_keep_rgb_or_explicit_grayscale(self):
        for language in ('de','en'):
            for name in ('start','wait','transition','reboot','final'):
                with self.subTest(language=language, name=name):
                    rgb = system_screens.screen_image(name,language,grayscale=False)
                    gray = system_screens.screen_image(name,language)
                    self.assertEqual(rgb.mode, 'RGB')
                    self.assertTrue({GOLD, CREAM, INK}.issubset(palette(rgb)))
                    self.assertEqual(gray.mode, 'L')
                    self.assertEqual(gray.tobytes(), rgb.convert('L').tobytes())
                    self.assertEqual(len(image_to_frame(gray).data),8192)

    def test_runtime_startup_preserves_gold_in_rgb565(self):
        frame = start_splash(DEFAULT_LOGO,'de',color=True)
        self.assertEqual(len(frame.data),32768)
        self.assertIn(rgb565(GOLD),frame_palette(frame))
        self.assertEqual(frame.data, image_to_frame(
            system_screens.screen_image('start','de',grayscale=False),color=True).data)

    def test_interactive_shutdown_preserves_logo_color(self):
        menu = InteractiveMenu()
        menu._show_shutdown_screen('reboot')
        frame = menu.frame(partial(image_to_frame,color=True),gray=False)
        self.assertIn(rgb565(GOLD),frame_palette(frame))
        self.assertEqual(frame.data,image_to_frame(system_screens.screen_image(
            'reboot',menu.language,grayscale=False),color=True).data)

    def test_firmware_splash_payload_contains_colored_logo(self):
        image = system_screens.screen_image('start','de',grayscale=False)
        source = build(image, DisplayPins(controller='ssd1351'))
        payload = b''.join(bytes.fromhex(line.split('] ',1)[1].replace('0x',''))
                           for line in source.splitlines() if '[DATA_ONLY]' in line)
        self.assertEqual(payload,image_to_frame(image,color=True).data)
        self.assertIn(rgb565(GOLD),{payload[i:i+2] for i in range(0,len(payload),2)})

    def test_native_shutdown_frames_contain_color_in_every_language(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'shutdown_frames.h'
            generate(ARTWORK/'shutdown_screen.png',ARTWORK/'pleasewait_screen.png',
                     ARTWORK/'reboot_screen.png',ARTWORK/'shutdown_screen.png',
                     output,'ssd1351',language='de',
                     display_config=DisplayPins(controller='ssd1351'))
            text = output.read_text()
        for language in ('de','en'):
            for phase in ('wait','transition','reboot','final'):
                with self.subTest(phase=phase,language=language):
                    match = re.search(r'shutdown_frame_'+phase+'_'+language+
                                      r'\[32768\] = \{(.*?)\};',text,re.S)
                    self.assertIsNotNone(match)
                    payload = bytes(int(value,16) for value in re.findall(r'0x([0-9a-f]{2})',match[1]))
                    self.assertEqual(payload,image_to_frame(system_screens.screen_image(
                        phase,language,grayscale=False),color=True).data)


if __name__ == '__main__':
    unittest.main()
