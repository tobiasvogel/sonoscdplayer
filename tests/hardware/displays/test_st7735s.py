"""ST7735S packing, command/data boundary, BL ownership and offline patterns."""
import contextlib
import io
import json
import sys
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from sonoscdplayer.ui.displays.backend import GraphicFrame
from sonoscdplayer.hardware.displays.transport import SPIConfig, SPITransport
from sonoscdplayer.hardware.displays.backlight import BacklightConfig, GPIOBacklight
from sonoscdplayer.hardware.displays.st7735s import DisplayConfig, ST7735S, image_to_frame, native_bytes
from sonoscdplayer.hardware.displays.st7735s_test import main, pattern_image


class PackingTests(unittest.TestCase):
    def test_rgb565_primary_colors_high_byte_first(self):
        image = Image.new('RGB', (128, 160))
        for x, color in enumerate(('red', 'lime', 'blue', 'white')):
            image.putpixel((x, 0), Image.new('RGB', (1, 1), color).getpixel((0, 0)))
        frame = image_to_frame(image)
        self.assertEqual(frame.data[:10], b'\xf8\x00\x07\xe0\x00\x1f\xff\xff\x00\x00')
        self.assertEqual(len(frame.data), 40960)

    def test_all_rotations_map_corners_without_changing_color_bytes(self):
        corners = {
            0: ((0, 0), (127, 0), (0, 159), (127, 159)),
            90: ((127, 0), (127, 159), (0, 0), (0, 159)),
            180: ((127, 159), (0, 159), (127, 0), (0, 0)),
            270: ((0, 159), (0, 0), (127, 159), (127, 0)),
        }
        for rotation, native in corners.items():
            config = DisplayConfig(rotation=rotation)
            width, height = config.size
            image = Image.new('RGB', config.size)
            for xy, color in zip(((0, 0), (width-1, 0), (0, height-1), (width-1, height-1)),
                                 ('red', 'lime', 'blue', 'white')):
                image.putpixel(xy, Image.new('RGB', (1, 1), color).getpixel((0, 0)))
            data = native_bytes(image_to_frame(image, config), config)
            for (x, y), value in zip(native, (b'\xf8\x00', b'\x07\xe0', b'\x00\x1f', b'\xff\xff')):
                with self.subTest(rotation=rotation, x=x, y=y):
                    offset = 2*(y*128+x)
                    self.assertEqual(data[offset:offset+2], value)
            self.assertEqual(sum(byte != 0 for byte in data), 6)

    def test_bad_config_and_image_size(self):
        for kwargs in (dict(rotation=True), dict(rotation=45), dict(x_offset=5),
                       dict(y_offset=3), dict(x_offset=True), dict(color_order='unknown')):
            with self.assertRaises(ValueError):
                DisplayConfig(**kwargs)
        with self.assertRaises(ValueError):
            image_to_frame(Image.new('RGB', (128, 128)))

    def test_exact_native_frame_and_wrong_format(self):
        frame = GraphicFrame(128, 160, 'rgb565', bytes(40960))
        self.assertIs(native_bytes(frame), frame.data)
        for bad in (GraphicFrame(128, 160, 'rgb565', bytes(40959)),
                    GraphicFrame(160, 128, 'rgb565', bytes(40960)),
                    GraphicFrame(128, 160, 'mono1-vlsb', bytes(2560))):
            with self.assertRaises(ValueError):
                native_bytes(bad)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.transport = Mock()
        for name in ('open', 'reset', 'command', 'data', 'close'):
            getattr(self.transport, name).side_effect = lambda *args, name=name: self.events.append((name, *args))
        self.bl = Mock()
        self.bl.open.side_effect = lambda: self.events.append(('bl_open',))
        self.bl.set.side_effect = lambda value: self.events.append(('bl_set', value))
        self.bl.close.side_effect = lambda: self.events.append(('bl_close',))
        self.sleep = Mock()
        self.driver = ST7735S(self.transport, self.bl, sleep=self.sleep)

    def test_initialization_keeps_backlight_off_until_first_frame(self):
        self.driver.open()
        self.assertEqual(self.events[:3], [('open',), ('bl_open',), ('reset',)])
        self.assertIn(('command', b'\x01'), self.events)
        self.assertIn(('command', b'\x11'), self.events)
        self.assertIn(('command', b'\x29'), self.events)
        self.assertIn(('data', bytes(40960)), self.events)
        self.bl.set.assert_not_called()
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [.12, .15, .12, .01, .1])
        count = len(self.events)
        self.driver.open()
        self.assertEqual(len(self.events), count)
        self.driver.show(GraphicFrame(128, 160, 'rgb565', bytes(40960)))
        self.assertEqual(self.events[-1], ('bl_set', True))

    def test_parameters_are_sent_as_data_not_command_bytes(self):
        self.driver.open()
        at = self.events.index(('command', b'\x3a'))
        self.assertEqual(self.events[at+1], ('data', b'\x05'))
        at = self.events.index(('command', b'\x36'))
        self.assertEqual(self.events[at+1], ('data', b'\xc0'))

    def test_each_frame_sets_inclusive_address_window_and_ramwr(self):
        self.driver.open()
        self.events.clear()
        data = bytes(range(256))*160
        self.driver.show(GraphicFrame(128, 160, 'rgb565', data))
        self.assertEqual(self.events, [
            ('command', b'\x2a'), ('data', b'\x00\x02\x00\x81'),
            ('command', b'\x2b'), ('data', b'\x00\x01\x00\xa0'),
            ('command', b'\x2c'), ('data', data), ('bl_set', True)])

    def test_diagnostic_offsets_and_rgb_order(self):
        driver = ST7735S(self.transport, self.bl,
                         DisplayConfig(x_offset=0, y_offset=0, color_order='rgb'), sleep=lambda _: None)
        driver.open()
        at = self.events.index(('command', b'\x36'))
        self.assertEqual(self.events[at+1], ('data', b'\xc0'))
        at = self.events.index(('command', b'\x2a'))
        self.assertEqual(self.events[at+1], ('data', b'\x00\x00\x00\x7f'))

    def test_bgr_override_remains_available(self):
        driver = ST7735S(self.transport, self.bl, DisplayConfig(color_order='bgr'), sleep=lambda _: None)
        driver.open()
        at = self.events.index(('command', b'\x36'))
        self.assertEqual(self.events[at+1], ('data', b'\xc8'))

    def test_invalid_frame_does_not_touch_bus_or_backlight(self):
        self.driver.open()
        self.events.clear()
        with self.assertRaises(ValueError):
            self.driver.show(GraphicFrame(128, 160, 'rgb565', b''))
        self.assertEqual(self.events, [])

    def test_unopened_display_cannot_show(self):
        with self.assertRaises(RuntimeError):
            self.driver.show(GraphicFrame(128, 160, 'rgb565', bytes(40960)))
        self.assertEqual(self.events, [])

    def test_startup_failure_releases_both_resources_without_lighting_panel(self):
        self.transport.command.side_effect = OSError('SPI failed')
        with self.assertRaisesRegex(OSError, 'SPI failed'):
            self.driver.open()
        self.bl.set.assert_not_called()
        self.bl.close.assert_called_once()
        self.transport.close.assert_called_once()
        self.assertFalse(self.driver.opened)

    def test_transfer_failure_does_not_turn_backlight_on(self):
        self.driver.open()
        self.transport.data.side_effect = OSError('transfer failed')
        with self.assertRaises(OSError):
            self.driver.show(GraphicFrame(128, 160, 'rgb565', bytes(40960)))
        self.bl.set.assert_not_called()
        self.driver.close()
        self.bl.close.assert_called_once()

    def test_close_turns_backlight_off_before_display_sleep(self):
        self.driver.open()
        self.events.clear()
        self.driver.close()
        self.assertEqual(self.events, [('bl_close',), ('command', b'\x28'),
                                       ('command', b'\x10'), ('close',)])

    def test_backlight_cleanup_failure_still_closes_spi(self):
        self.driver.open()
        self.bl.close.side_effect = OSError('BL failed')
        with self.assertRaises(OSError):
            self.driver.close()
        self.transport.close.assert_called_once()
        self.assertFalse(self.driver.opened)

    def test_display_off_failure_still_closes_spi(self):
        self.driver.open()
        self.transport.command.side_effect = OSError('off failed')
        with self.assertRaises(OSError):
            self.driver.close()
        self.bl.close.assert_called_once()
        self.transport.close.assert_called_once()

    def test_backlight_pinmux_check_failure_closes_bus_before_any_command(self):
        self.bl.open.side_effect = ValueError('MISO still claimed')
        with self.assertRaisesRegex(ValueError, 'MISO still claimed'):
            self.driver.open()
        self.transport.command.assert_not_called()
        self.transport.reset.assert_not_called()
        self.transport.close.assert_called_once()


class BacklightTests(unittest.TestCase):
    def setUp(self):
        self.gpio = Mock(BCM=11, OUT=0, HIGH=1, LOW=0)
        self.bl = GPIOBacklight(BacklightConfig(18), gpio=self.gpio)

    def test_open_low_set_high_close_low_only_owned_pin(self):
        self.bl.open()
        self.bl.open()
        self.gpio.setup.assert_called_once_with(18, 0, initial=0)
        self.bl.set(True)
        self.gpio.output.assert_called_with(18, 1)
        self.bl.close()
        self.gpio.output.assert_called_with(18, 0)
        self.gpio.cleanup.assert_called_once_with([18])
        self.bl.close()
        self.gpio.cleanup.assert_called_once()

    def test_reserved_and_overlapping_pins(self):
        for pin in (True, -1, 28, 7, 8, 9, 10, 11):
            with self.assertRaises(ValueError):
                BacklightConfig(pin)
        for pin in (24, 25):
            with self.assertRaises(ValueError):
                BacklightConfig(pin).validate_control_pins(SPIConfig())

    def test_unopened_and_non_boolean_state(self):
        with self.assertRaises(RuntimeError):
            self.bl.set(True)
        self.bl.open()
        with self.assertRaises(ValueError):
            self.bl.set(1)

    def test_pin_setup_failure_still_attempts_off_and_cleanup(self):
        self.gpio.setup.side_effect = OSError('setup failed')
        with self.assertRaisesRegex(OSError, 'setup failed'):
            self.bl.open()
        self.gpio.cleanup.assert_called_once_with([18])
        self.assertFalse(self.bl.opened)

    def test_off_failure_still_cleans_owned_pin(self):
        self.bl.open()
        self.gpio.output.side_effect = OSError('off failed')
        with self.assertRaises(OSError):
            self.bl.close()
        self.gpio.cleanup.assert_called_once_with([18])

    def test_gpio9_checks_input_mux_then_drives_low_on_and_off(self):
        gpio = Mock(BCM=11, OUT=0, IN=1, HIGH=1, LOW=0)
        gpio.gpio_function.return_value = gpio.IN
        bl = GPIOBacklight(BacklightConfig(9, True), gpio=gpio)
        bl.open()
        gpio.gpio_function.assert_called_once_with(9)
        gpio.setup.assert_called_once_with(9, gpio.OUT, initial=gpio.LOW)
        bl.set(True)
        gpio.output.assert_called_with(9, gpio.HIGH)
        bl.close()
        gpio.output.assert_called_with(9, gpio.LOW)
        gpio.cleanup.assert_called_once_with([9])

    def test_gpio9_rejects_alternate_function_without_setup_or_cleanup(self):
        gpio = Mock(BCM=11, OUT=0, IN=1, HIGH=1, LOW=0)
        gpio.gpio_function.return_value = 41
        bl = GPIOBacklight(BacklightConfig(9, True), gpio=gpio)
        with self.assertRaisesRegex(ValueError, 'no_miso'):
            bl.open()
        gpio.setup.assert_not_called()
        gpio.output.assert_not_called()
        gpio.cleanup.assert_not_called()
        self.assertFalse(bl.opened)

    def test_gpio9_release_flag_must_be_boolean(self):
        with self.assertRaises(ValueError):
            BacklightConfig(9, 1)


class DiagnosticTests(unittest.TestCase):
    def test_offline_all_patterns_need_no_pin_or_hardware_imports(self):
        with patch.dict(sys.modules, {'RPi.GPIO': None, 'spidev': None}), \
             patch('sonoscdplayer.hardware.displays.st7735s_test.SPITransport') as spi, \
             patch('sonoscdplayer.hardware.displays.st7735s_test.GPIOBacklight') as bl, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            main(['--pattern', 'all'])
        spi.assert_not_called()
        bl.assert_not_called()
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r['pattern'] for r in reports], ['colors', 'grid', 'checker', 'text', 'clear'])
        self.assertTrue(all(r['bytes'] == 40960 and not r['hardware'] for r in reports))
        self.assertTrue(all(r['backlight_pin'] is None for r in reports))

    def test_invalid_options_and_missing_bl_pin_fail_before_hardware(self):
        for args in (['--gpio', '--execute'], ['--execute'], ['--seconds', 'nan'],
                     ['--seconds', 'inf'], ['--backlight-pin', '22'],
                     ['--backlight-pin', '27'], ['--backlight-pin', '10'],
                     ['--x-offset', '5'], ['--color-order', 'unknown']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                 patch('sonoscdplayer.hardware.displays.st7735s_test.SPITransport') as spi, \
                 self.assertRaises(SystemExit):
                main(args)
            spi.assert_not_called()

    def test_hardware_failure_still_closes_driver(self):
        driver = Mock()
        driver.show.side_effect = OSError('SPI failed')
        with patch('sonoscdplayer.hardware.displays.st7735s_test.ST7735S', return_value=driver), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
            main(['--gpio', '--execute', '--backlight-pin', '18', '--pattern', 'grid'])
        driver.close.assert_called_once()

    def test_grid_has_four_edges_and_distinct_corner_markers(self):
        image = pattern_image('grid')
        for x in range(128):
            self.assertEqual(image.getpixel((x, 0)), (255, 255, 255))
            self.assertEqual(image.getpixel((x, 159)), (255, 255, 255))
        self.assertEqual(image.getpixel((5, 5)), (255, 0, 0))
        self.assertEqual(image.getpixel((120, 5)), (0, 255, 0))
        self.assertEqual(image.getpixel((5, 152)), (0, 0, 255))
        self.assertEqual(image.getpixel((115, 147)), (255, 255, 0))

    def test_clear_is_black_and_all_pattern_sizes_follow_rotation(self):
        for rotation in (0, 90, 180, 270):
            config = DisplayConfig(rotation=rotation)
            for pattern in ('colors', 'grid', 'checker', 'text', 'clear'):
                self.assertEqual(pattern_image(pattern, config).size, config.size)
            self.assertIsNone(pattern_image('clear', config).getbbox())

    def test_large_frame_is_chunked_by_existing_spi_transport(self):
        spi, gpio = Mock(), Mock(BCM=11, OUT=0, HIGH=1, LOW=0)
        transport = SPITransport(spi=spi, gpio=gpio)
        transport.open()
        transport.data(bytes(40960))
        self.assertEqual([len(c.args[0]) for c in spi.writebytes.call_args_list], [4096]*10)
        transport.close()

    def test_gpio9_cli_selects_explicit_release_without_touching_real_hardware(self):
        driver = Mock()
        with patch('sonoscdplayer.hardware.displays.st7735s_test.ST7735S', return_value=driver), \
             patch('sonoscdplayer.hardware.displays.st7735s_test.GPIOBacklight') as backlight, \
             patch('sonoscdplayer.hardware.displays.st7735s_test.time.sleep'), \
             contextlib.redirect_stdout(io.StringIO()):
            main(['--gpio', '--execute', '--backlight-pin', '9',
                  '--spi-miso-released', '--pattern', 'grid'])
        backlight.assert_called_once_with(BacklightConfig(9, True), gpio_warnings=False)
        driver.show.assert_called_once()
        driver.close.assert_called_once()

    def test_gpio9_without_release_flag_fails_before_hardware(self):
        with patch('sonoscdplayer.hardware.displays.st7735s_test.SPITransport') as spi, \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['--gpio', '--execute', '--backlight-pin', '9'])
        spi.assert_not_called()


if __name__ == '__main__':
    unittest.main()
