"""SSD1351 SPI commands, canonical RGB565, brightness and offline diagnostic."""
import contextlib
import io
import json
import sys
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from sonoscdplayer.ui.displays.backend import GraphicFrame
from sonoscdplayer.hardware.displays.transport import SPIConfig, SPITransport
from sonoscdplayer.hardware.displays.ssd1351 import (
    CAPABILITIES, DisplayConfig, SSD1351, image_to_frame, native_bytes,
)
from sonoscdplayer.hardware.displays.ssd1351_test import main, pattern_image


class PackingTests(unittest.TestCase):
    def test_primary_colors_high_byte_first(self):
        image = Image.new('RGB', (128, 128))
        for x, color in enumerate(('red', 'lime', 'blue', 'white')):
            image.paste(color, (x, 0, x+1, 1))
        frame = image_to_frame(image)
        self.assertEqual(frame.data[:10], b'\xf8\x00\x07\xe0\x00\x1f\xff\xff\x00\x00')
        self.assertEqual(len(frame.data), 32768)
        self.assertEqual((frame.width, frame.height, frame.pixel_format), (128, 128, 'rgb565'))

    def test_all_rotations_map_distinct_corners(self):
        image = Image.new('RGB', (128, 128))
        for box, color in zip(((0, 0, 1, 1), (127, 0, 128, 1),
                               (0, 127, 1, 128), (127, 127, 128, 128)),
                              ('red', 'lime', 'blue', 'white')):
            image.paste(color, box)
        frame = image_to_frame(image)
        corners = {
            0: ((0, 0), (127, 0), (0, 127), (127, 127)),
            90: ((127, 0), (127, 127), (0, 0), (0, 127)),
            180: ((127, 127), (0, 127), (127, 0), (0, 0)),
            270: ((0, 127), (0, 0), (127, 127), (127, 0)),
        }
        for rotation, coordinates in corners.items():
            data = native_bytes(frame, DisplayConfig(rotation=rotation))
            for (x, y), expected in zip(coordinates, (b'\xf8\x00', b'\x07\xe0', b'\x00\x1f', b'\xff\xff')):
                offset = 2*(y*128+x)
                self.assertEqual(data[offset:offset+2], expected)

    def test_invalid_frames_and_image_sizes(self):
        for image_size in ((128, 160), (64, 128)):
            with self.assertRaises(ValueError): image_to_frame(Image.new('RGB', image_size))
        for frame in (GraphicFrame(128, 128, 'rgb565', bytes(32767)),
                      GraphicFrame(128, 128, 'gray4-packed', bytes(8192)),
                      GraphicFrame(64, 128, 'rgb565', bytes(16384))):
            with self.assertRaises(ValueError): native_bytes(frame)

    def test_invalid_configuration(self):
        for kwargs in (dict(rotation=True), dict(rotation=45), dict(color_order='unknown'),
                       dict(brightness=True), dict(brightness=-1), dict(brightness=16)):
            with self.assertRaises(ValueError): DisplayConfig(**kwargs)
        self.assertEqual(CAPABILITIES.pixel_format, 'rgb565')


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.transport = Mock()
        for name in ('open', 'reset', 'command', 'data', 'close'):
            getattr(self.transport, name).side_effect = lambda *args, name=name: self.events.append((name, *args))
        self.sleep = Mock()
        self.driver = SSD1351(self.transport, sleep=self.sleep)
        self.frame = GraphicFrame(128, 128, 'rgb565', bytes(32768))

    def test_unlock_setup_and_black_ram_before_display_on(self):
        self.driver.open()
        expected = [('open',), ('reset',)]
        # SSD1351 command/parameter vectors, independent of production loops.
        for cmd, params in (
            (0xFD, b'\x12'), (0xFD, b'\xb1'), (0xAE, b''),
            (0xB3, b'\xf1'), (0xCA, b'\x7f'), (0xA0, b'\x74'),
            (0xA1, b'\x00'), (0xA2, b'\x00'), (0xB5, b'\x00'),
            (0xAB, b'\x01'), (0xB1, b'\x32'), (0xBE, b'\x05'),
            (0xA6, b''), (0xC1, b'\xc8\x80\xc8'), (0xC7, b'\x08'),
            (0xB4, b'\xa0\xb5\x55'), (0xB6, b'\x01'),
            (0x15, b'\x00\x7f'), (0x75, b'\x00\x7f'), (0x5C, b''),
        ):
            expected.append(('command', bytes([cmd])))
            if params: expected.append(('data', params))
        expected.extend([('data', bytes(32768)), ('command', b'\xaf')])
        self.assertEqual(self.events, expected)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [.3, .1])

    def test_each_show_sets_window_and_write_ram_command(self):
        self.driver.open()
        self.events.clear()
        self.driver.show(self.frame)
        self.assertEqual(self.events, [('command', b'\x15'), ('data', b'\x00\x7f'),
                                      ('command', b'\x75'), ('data', b'\x00\x7f'),
                                      ('command', b'\x5c'), ('data', self.frame.data)])

    def test_color_remap_override(self):
        driver = SSD1351(self.transport, DisplayConfig(color_order='bgr'), sleep=lambda _: None)
        driver.open()
        at = self.events.index(('command', b'\xa0'))
        self.assertEqual(self.events[at+1], ('data', b'\x70'))

    def test_brightness_only_writes_c7_without_rewriting_pixels(self):
        self.driver.open()
        self.events.clear()
        self.driver.set_brightness(0)
        self.driver.set_brightness(15)
        self.assertEqual(self.events, [('command', b'\xc7'), ('data', b'\x00'),
                                      ('command', b'\xc7'), ('data', b'\x0f')])
        for value in (True, -1, 16, 1.5):
            with self.assertRaises(ValueError): self.driver.set_brightness(value)

    def test_configured_brightness_is_used_on_open(self):
        driver = SSD1351(self.transport, DisplayConfig(brightness=3), sleep=lambda _: None)
        driver.open()
        at = self.events.index(('command', b'\xc7'))
        self.assertEqual(self.events[at+1], ('data', b'\x03'))

    def test_unopened_calls_and_invalid_frame_do_not_write(self):
        with self.assertRaises(RuntimeError): self.driver.show(self.frame)
        with self.assertRaises(RuntimeError): self.driver.set_brightness(8)
        self.assertEqual(self.events, [])
        self.driver.open()
        self.events.clear()
        with self.assertRaises(ValueError):
            self.driver.show(GraphicFrame(128, 128, 'rgb565', b''))
        self.assertEqual(self.events, [])

    def test_idempotent_open_close_and_reopen(self):
        self.driver.open()
        count = len(self.events)
        self.driver.open()
        self.assertEqual(len(self.events), count)
        self.driver.close()
        self.assertEqual(self.events[-2:], [('command', b'\xae'), ('close',)])
        self.driver.close()
        self.transport.close.assert_called_once()
        self.driver.open()
        self.assertEqual(self.transport.open.call_count, 2)

    def test_initialization_failure_releases_transport_and_preserves_error(self):
        self.transport.command.side_effect = OSError('SPI write failed')
        with self.assertRaisesRegex(OSError, 'SPI write failed'): self.driver.open()
        self.assertFalse(self.driver.opened)
        self.transport.close.assert_called_once()

    def test_interrupted_startup_after_display_on_turns_display_off(self):
        self.sleep.side_effect = [None, KeyboardInterrupt]
        with self.assertRaises(KeyboardInterrupt): self.driver.open()
        self.assertEqual(self.events[-2:], [('command', b'\xae'), ('close',)])
        self.assertFalse(self.driver.opened)

    def test_failed_display_off_still_closes(self):
        self.driver.open()
        self.transport.command.side_effect = OSError('off failed')
        with self.assertRaises(OSError): self.driver.close()
        self.transport.close.assert_called_once()
        self.assertFalse(self.driver.opened)


class DiagnosticTests(unittest.TestCase):
    def test_offline_all_patterns_without_hardware_import_or_network(self):
        import socket
        with patch.dict(sys.modules, {'RPi.GPIO': None, 'spidev': None}), \
             patch.object(socket, 'socket', side_effect=AssertionError('network access')), \
             patch('sonoscdplayer.hardware.displays.ssd1351_test.SPITransport') as transport, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            main([])
        transport.assert_not_called()
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r['pattern'] for r in reports],
                         ['colors', 'grid', 'checker', 'text'] + ['brightness']*4 + ['clear'])
        self.assertTrue(all(r['bytes'] == 32768 and not r['hardware'] for r in reports))
        levels = [r['brightness'] for r in reports if r['pattern'] == 'brightness']
        self.assertEqual(levels, [1, 5, 10, 15])

    def test_bad_options_never_construct_hardware(self):
        for args in (['--execute'], ['--gpio'], ['--seconds', 'nan'], ['--seconds', 'inf'],
                     ['--brightness', '16'], ['--dc-pin', '9'], ['--rst-pin', '25'],
                     ['--backlight-pin', '9'], ['--rotation', '45']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                 patch('sonoscdplayer.hardware.displays.ssd1351_test.SPITransport') as transport, \
                 self.assertRaises(SystemExit): main(args)
            transport.assert_not_called()

    def test_brightness_demo_and_regular_brightness_argument(self):
        driver = Mock()
        with patch('sonoscdplayer.hardware.displays.ssd1351_test.SSD1351', return_value=driver), \
             patch('sonoscdplayer.hardware.displays.ssd1351_test.time.sleep'), \
             contextlib.redirect_stdout(io.StringIO()):
            main(['--gpio', '--execute', '--pattern', 'brightness'])
        self.assertEqual([c.args[0] for c in driver.set_brightness.call_args_list], [1, 5, 10, 15])
        self.assertEqual(driver.show.call_count, 4)
        driver.close.assert_called_once()

    def test_hardware_failure_preserved_when_cleanup_also_fails(self):
        driver = Mock()
        driver.show.side_effect = OSError('frame failed')
        driver.close.side_effect = OSError('off failed')
        with patch('sonoscdplayer.hardware.displays.ssd1351_test.SSD1351', return_value=driver), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaisesRegex(OSError, 'frame failed'):
            main(['--gpio', '--execute', '--pattern', 'grid'])
        driver.close.assert_called_once()

    def test_pattern_geometry_corner_colors_and_clear(self):
        grid = pattern_image('grid')
        for at in range(128):
            for xy in ((at, 0), (at, 127), (0, at), (127, at)):
                self.assertEqual(grid.getpixel(xy), (255, 255, 255))
        self.assertEqual(grid.getpixel((5, 5)), (255, 0, 0))
        self.assertEqual(grid.getpixel((120, 5)), (0, 255, 0))
        self.assertEqual(grid.getpixel((5, 120)), (0, 0, 255))
        self.assertEqual(grid.getpixel((115, 115)), (255, 255, 0))
        self.assertIsNone(pattern_image('clear').getbbox())

    def test_shared_spi_chunks_and_only_dc_reset_are_claimed(self):
        spi, gpio = Mock(), Mock(BCM=11, OUT=1, LOW=0, HIGH=1)
        transport = SPITransport(SPIConfig(dc_pin=22, rst_pin=27), spi=spi, gpio=gpio, sleep=lambda _: None)
        driver = SSD1351(transport, sleep=lambda _: None)
        driver.open()
        spi.writebytes.reset_mock()
        driver.show(GraphicFrame(128, 128, 'rgb565', bytes(32768)))
        chunks = [c.args[0] for c in spi.writebytes.call_args_list if len(c.args[0]) > 2]
        self.assertEqual([len(chunk) for chunk in chunks], [4096]*8)
        self.assertEqual([c.args[0] for c in gpio.setup.call_args_list], [22, 27])
        driver.close()
        gpio.cleanup.assert_called_once_with([22, 27])


if __name__ == '__main__': unittest.main()
