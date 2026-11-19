"""SH1107 packing, protocol, SPI lifecycle and offline diagnostic tests."""
import contextlib
import io
import json
import sys
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from sonoscdplayer.ui.displays.backend import GraphicFrame, validate_frame
from sonoscdplayer.hardware.displays.transport import SPIConfig, SPITransport
from sonoscdplayer.hardware.displays.sh1107 import (
    DisplayConfig, SH1107, image_to_frame, native_bytes, PIXEL_FORMAT,
)
from sonoscdplayer.hardware.displays.sh1107_test import main, pattern_image


class PackingTests(unittest.TestCase):
    def test_vertical_bit_order_and_page_boundary(self):
        config = DisplayConfig(rotation=0)
        image = Image.new('L', config.size)
        for xy in ((0, 0), (0, 7), (1, 8), (63, 127)):
            image.putpixel(xy, 255)
        frame = image_to_frame(image, config)
        expected = bytearray(1024)
        expected[0], expected[65], expected[-1] = 0x81, 1, 0x80
        self.assertEqual(frame.data, bytes(expected))

    def test_rotation_maps_all_four_distinct_corners(self):
        corners = {
            0: ((0, 0), (63, 0), (0, 127), (63, 127)),
            90: ((63, 0), (63, 127), (0, 0), (0, 127)),
            180: ((63, 127), (0, 127), (63, 0), (0, 0)),
            270: ((0, 127), (0, 0), (63, 127), (63, 0)),
        }
        for rotation, native in corners.items():
            config = DisplayConfig(rotation=rotation)
            w, h = config.size
            for logical, (nx, ny) in zip(((0, 0), (w-1, 0), (0, h-1), (w-1, h-1)), native):
                with self.subTest(rotation=rotation, logical=logical):
                    image = Image.new('1', config.size)
                    image.putpixel(logical, 1)
                    expected = bytearray(1024)
                    expected[(ny // 8) * 64 + nx] = 1 << (ny % 8)
                    self.assertEqual(native_bytes(image_to_frame(image, config), config), expected)

    def test_threshold_and_exact_size(self):
        config = DisplayConfig()
        image = Image.new('L', config.size)
        image.putpixel((0, 0), 127)
        image.putpixel((1, 0), 128)
        frame = image_to_frame(image, config)
        self.assertEqual(frame.data[:2], b'\x00\x01')
        with self.assertRaises(ValueError):
            image_to_frame(Image.new('1', (128, 128)))

    def test_contract_rejects_wrong_geometry_format_and_length(self):
        cap = DisplayConfig().capabilities
        validate_frame(cap, GraphicFrame(128, 64, PIXEL_FORMAT, bytes(1024)))
        for frame in (GraphicFrame(64, 128, PIXEL_FORMAT, bytes(1024)),
                      GraphicFrame(128, 64, 'gray4-packed', bytes(4096)),
                      GraphicFrame(128, 64, PIXEL_FORMAT, bytes(1023))):
            with self.assertRaises(ValueError):
                validate_frame(cap, frame)

    def test_config_validation(self):
        for kwargs in (dict(rotation=True), dict(rotation=45), dict(contrast=-1),
                       dict(contrast=True), dict(display_offset=128)):
            with self.assertRaises(ValueError):
                DisplayConfig(**kwargs)

    def test_contract_rejects_incomplete_vertical_page(self):
        from sonoscdplayer.ui.displays.capabilities import DisplayCapabilities
        cap = DisplayCapabilities(kind='graphic', width=128, height=63,
                                  pixel_format=PIXEL_FORMAT)
        with self.assertRaises(ValueError):
            validate_frame(cap, GraphicFrame(128, 63, PIXEL_FORMAT, bytes(1008)))


class RecordingTransport:
    def __init__(self):
        self.events = []

    def open(self): self.events.append(('open',))
    def reset(self): self.events.append(('reset',))
    def command(self, payload): self.events.append(('command', payload))
    def data(self, payload): self.events.append(('data', payload))
    def close(self): self.events.append(('close',))


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.transport = RecordingTransport()
        self.sleep = Mock()
        self.driver = SH1107(self.transport, sleep=self.sleep)

    def test_open_clears_native_ram_before_enabling_panel(self):
        self.driver.open()
        events = self.transport.events
        self.assertEqual(events[:3], [('open',), ('reset',), ('command', b'\xae')])
        self.assertIn(('command', b'\xa8\x7f'), events)
        self.assertIn(('command', b'\xd3\x60'), events)
        self.assertIn(('command', b'\x20'), events)
        self.assertIn(('command', b'\xad\x81'), events)
        data = [value for kind, *values in events if kind == 'data' for value in values]
        self.assertEqual(data, [bytes(64)] * 16)
        self.assertEqual(events[-1], ('command', b'\xaf'))
        self.sleep.assert_called_once_with(.1)
        count = len(events)
        self.driver.open()
        self.assertEqual(len(events), count)

    def test_show_addresses_every_page_and_sends_exact_bytes(self):
        self.driver.open()
        self.transport.events.clear()
        # Distinct data on each page catches page order and data truncation.
        config = DisplayConfig(rotation=0)
        driver = SH1107(self.transport, config)
        driver.opened = True
        data = b''.join(bytes([page]) * 64 for page in range(16))
        driver.show(GraphicFrame(64, 128, PIXEL_FORMAT, data))
        expected = []
        for page in range(16):
            expected += [('command', bytes([0xb0 | page, 0, 0x10])),
                         ('data', bytes([page]) * 64)]
        self.assertEqual(self.transport.events, expected)

    def test_bad_frame_does_not_write(self):
        self.driver.open()
        self.transport.events.clear()
        with self.assertRaises(ValueError):
            self.driver.show(GraphicFrame(128, 64, PIXEL_FORMAT, b''))
        self.assertEqual(self.transport.events, [])

    def test_lifecycle_and_contrast(self):
        with self.assertRaises(RuntimeError):
            self.driver.set_contrast(128)
        self.driver.open()
        self.driver.set_contrast(0)
        self.assertEqual(self.transport.events[-1], ('command', b'\x81\x00'))
        for value in (True, -1, 256):
            with self.assertRaises(ValueError):
                self.driver.set_contrast(value)
        self.driver.close()
        self.assertEqual(self.transport.events[-2:], [('command', b'\xae'), ('close',)])
        with self.assertRaises(RuntimeError):
            self.driver.show(GraphicFrame(128, 64, PIXEL_FORMAT, bytes(1024)))

    def test_open_failure_preserves_error_and_releases_transport(self):
        self.transport.command = Mock(side_effect=OSError('write failed'))
        with self.assertRaisesRegex(OSError, 'write failed'):
            self.driver.open()
        self.assertFalse(self.driver.opened)
        self.assertEqual(self.transport.events[-1], ('close',))

    def test_display_off_failure_still_releases_transport(self):
        self.driver.open()
        self.transport.command = Mock(side_effect=OSError('off failed'))
        with self.assertRaises(OSError):
            self.driver.close()
        self.assertFalse(self.driver.opened)
        self.assertEqual(self.transport.events[-1], ('close',))

    def test_page_write_failure_propagates_and_can_be_closed(self):
        self.driver.open()
        self.transport.data = Mock(side_effect=OSError('page failed'))
        with self.assertRaisesRegex(OSError, 'page failed'):
            self.driver.show(GraphicFrame(128, 64, PIXEL_FORMAT, bytes(1024)))
        self.driver.close()
        self.assertEqual(self.transport.events[-1], ('close',))

    def test_interrupted_startup_turns_panel_off_and_closes(self):
        self.sleep.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.driver.open()
        self.assertEqual(self.transport.events[-2:], [('command', b'\xae'), ('close',)])
        self.assertFalse(self.driver.opened)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.spi = Mock()
        self.gpio = Mock(BCM=11, OUT=0, HIGH=1, LOW=0)
        self.sleep = Mock()
        self.transport = SPITransport(spi=self.spi, gpio=self.gpio, sleep=self.sleep)

    def test_mode_reset_command_data_and_owned_pin_cleanup(self):
        self.transport.open()
        self.spi.open.assert_called_once_with(0, 0)
        self.assertEqual(self.spi.mode, 0)
        self.assertEqual(self.spi.max_speed_hz, 2_000_000)
        self.transport.reset()
        self.assertEqual([c.args for c in self.gpio.output.call_args_list],
                         [(24, 1), (24, 0), (24, 1)])
        self.transport.command(b'\x81\x80')
        self.gpio.output.assert_called_with(25, 0)
        self.spi.writebytes.assert_called_with([0x81, 0x80])
        self.transport.data(bytes(5000))
        self.gpio.output.assert_called_with(25, 1)
        self.assertEqual([len(c.args[0]) for c in self.spi.writebytes.call_args_list],
                         [2, 4096, 904])
        self.transport.close()
        self.gpio.cleanup.assert_called_once_with([25, 24])
        self.spi.close.assert_called_once()
        self.transport.close()
        self.spi.close.assert_called_once()

    def test_open_failure_closes_spi(self):
        self.gpio.setup.side_effect = OSError('setup failed')
        with self.assertRaises(OSError):
            self.transport.open()
        self.assertFalse(self.transport.opened)
        self.spi.close.assert_called_once()
        self.gpio.cleanup.assert_called_once_with([25])

    def test_spi_close_failure_still_cleans_gpio(self):
        self.transport.open()
        self.spi.close.side_effect = OSError('close failed')
        with self.assertRaises(OSError):
            self.transport.close()
        self.gpio.cleanup.assert_called_once_with([25, 24])

    def test_unopened_transport_cannot_write(self):
        for method in (self.transport.command, self.transport.data):
            with self.assertRaises(RuntimeError):
                method(b'\x00')

    def test_pin_and_bus_validation(self):
        for kwargs in (dict(bus=1), dict(device=True), dict(dc_pin=8),
                       dict(rst_pin=25), dict(dc_pin=-1), dict(spi_hz=0)):
            with self.assertRaises(ValueError):
                SPIConfig(**kwargs)


class DiagnosticTests(unittest.TestCase):
    def test_offline_does_not_import_hardware_or_open_bus(self):
        with patch.dict(sys.modules, {'spidev': None, 'RPi.GPIO': None}), \
             patch.object(SPITransport, 'open') as opened, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            main(['--pattern', 'all'])
        opened.assert_not_called()
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r['pattern'] for r in reports], ['grid', 'checker', 'text', 'clear'])
        self.assertTrue(all(r['bytes'] == 1024 and not r['hardware'] for r in reports))

    def test_invalid_cli_never_opens_hardware(self):
        for args in (['--execute'], ['--gpio'], ['--seconds', 'nan'],
                     ['--seconds', 'inf'], ['--dc-pin', '8'], ['--rotation', '45']):
            with patch.object(SPITransport, 'open') as opened, \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args)
            opened.assert_not_called()

    def test_hardware_failure_still_closes_driver(self):
        driver = Mock()
        driver.show.side_effect = OSError('display failed')
        with patch('sonoscdplayer.hardware.displays.sh1107_test.SH1107', return_value=driver), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
            main(['--gpio', '--execute', '--pattern', 'grid'])
        driver.close.assert_called_once()

    def test_patterns_cover_all_edges_and_clear(self):
        grid = pattern_image('grid')
        for x in range(128):
            self.assertTrue(grid.getpixel((x, 0)))
            self.assertTrue(grid.getpixel((x, 63)))
        for y in range(64):
            self.assertTrue(grid.getpixel((0, y)))
            self.assertTrue(grid.getpixel((127, y)))
        self.assertFalse(pattern_image('clear').getbbox())


if __name__ == '__main__':
    unittest.main()
