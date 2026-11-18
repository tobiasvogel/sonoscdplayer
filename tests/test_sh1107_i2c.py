"""I2C wire framing, SPI equivalence, lifecycle and diagnostic selection."""
import contextlib
import io
import json
import sys
import unittest
from unittest.mock import Mock, patch

from integrations.outputs.display_transport import (
    I2CConfig, I2CTransport, SPITransport,
)
from integrations.outputs.sh1107 import DisplayConfig, SH1107, image_to_frame
from integrations.outputs.sh1107_test import main, pattern_image


class I2CTransportTests(unittest.TestCase):
    def setUp(self):
        self.bus = Mock()
        self.gpio = Mock(BCM=11, OUT=0, HIGH=1, LOW=0)
        self.sleep = Mock()
        self.transport = I2CTransport(bus=self.bus, gpio=self.gpio, sleep=self.sleep)

    def test_control_bytes_address_and_32_byte_limit(self):
        self.transport.open()
        self.transport.command(b'\x81\x80')
        payload = bytes(range(65))
        self.transport.data(payload)
        calls = [c.args for c in self.bus.write_i2c_block_data.call_args_list]
        self.assertEqual(calls, [
            (0x3C, 0x00, [0x81, 0x80]),
            (0x3C, 0x40, list(range(32))),
            (0x3C, 0x40, list(range(32, 64))),
            (0x3C, 0x40, [64]),
        ])

    def test_reset_owns_only_reset_pin_and_close_is_idempotent(self):
        self.transport.open()
        self.transport.open()
        self.bus.open.assert_called_once_with(1)
        self.gpio.setup.assert_called_once_with(24, 0, initial=1)
        self.transport.reset()
        self.assertEqual([c.args for c in self.gpio.output.call_args_list],
                         [(24, 1), (24, 0), (24, 1)])
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [.001, .02, .02])
        self.transport.close()
        self.transport.close()
        self.gpio.cleanup.assert_called_once_with([24])
        self.bus.close.assert_called_once()

    def test_configurable_bus_address_and_reset(self):
        transport = I2CTransport(I2CConfig(bus=3, address=0x3D, rst_pin=27),
                                 bus=self.bus, gpio=self.gpio)
        transport.open()
        transport.command(b'\xae')
        self.bus.open.assert_called_once_with(3)
        self.bus.write_i2c_block_data.assert_called_once_with(0x3D, 0, [0xAE])
        transport.close()
        self.gpio.cleanup.assert_called_once_with([27])

    def test_configuration_rejects_shifted_address_and_bus_pin_overlap(self):
        for kwargs in (dict(address=0x78), dict(address=0x00), dict(address=True),
                       dict(bus=-1), dict(bus=True), dict(bus=256),
                       dict(rst_pin=True), dict(rst_pin=2), dict(rst_pin=3),
                       dict(rst_pin=28), dict(bus=0, rst_pin=0), dict(bus=0, rst_pin=1)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                I2CConfig(**kwargs)

    def test_bus_open_failure_releases_handle_without_touching_gpio(self):
        self.bus.open.side_effect = OSError('bus missing')
        with self.assertRaisesRegex(OSError, 'bus missing'):
            self.transport.open()
        self.bus.close.assert_called_once()
        self.gpio.setup.assert_not_called()
        self.gpio.cleanup.assert_not_called()
        self.assertFalse(self.transport.opened)

    def test_gpio_failure_closes_bus_and_cleans_pin(self):
        self.gpio.setup.side_effect = OSError('pin failed')
        with self.assertRaisesRegex(OSError, 'pin failed'):
            self.transport.open()
        self.bus.close.assert_called_once()
        self.gpio.cleanup.assert_called_once_with([24])
        self.assertFalse(self.transport.opened)

    def test_close_failure_still_releases_gpio(self):
        self.transport.open()
        self.bus.close.side_effect = OSError('close failed')
        with self.assertRaises(OSError):
            self.transport.close()
        self.gpio.cleanup.assert_called_once_with([24])
        self.assertFalse(self.transport.opened)

    def test_unopened_and_invalid_payload_never_write(self):
        for method in (self.transport.command, self.transport.data):
            with self.assertRaises(RuntimeError):
                method(b'\xaf')
        self.transport.open()
        for payload in ([], bytearray(1), 'text'):
            with self.assertRaises(TypeError):
                self.transport.data(payload)
        self.transport.data(b'')
        self.bus.write_i2c_block_data.assert_not_called()

    def test_write_failure_propagates_without_retry_or_remaining_chunks(self):
        self.transport.open()
        self.bus.write_i2c_block_data.side_effect = OSError('NACK')
        with self.assertRaisesRegex(OSError, 'NACK'):
            self.transport.data(bytes(64))
        self.bus.write_i2c_block_data.assert_called_once()
        self.transport.close()
        self.bus.close.assert_called_once()

    def test_open_close_open_reacquires_bus(self):
        self.transport.open()
        self.transport.close()
        self.transport.open()
        self.assertEqual(self.bus.open.call_count, 2)
        self.transport.close()
        self.assertEqual(self.bus.close.call_count, 2)


class EquivalenceTests(unittest.TestCase):
    @staticmethod
    def normalize(events):
        result = []
        for kind, payload in events:
            if result and result[-1][0] == kind:
                result[-1] = (kind, result[-1][1] + payload)
            else:
                result.append((kind, payload))
        return result

    def test_same_controller_and_all_patterns_have_identical_spi_i2c_streams(self):
        spi_events, i2c_events = [], []
        gpio = Mock(BCM=11, OUT=0, HIGH=1, LOW=0)
        dc = [0]
        gpio.output.side_effect = lambda pin, value: dc.__setitem__(0, value) if pin == 25 else None
        spi = Mock()
        spi.writebytes.side_effect = lambda payload: spi_events.append((dc[0], bytes(payload)))
        bus = Mock()
        bus.write_i2c_block_data.side_effect = lambda address, control, payload: i2c_events.append(
            (1 if control == 0x40 else 0, bytes(payload)))
        transports = (SPITransport(spi=spi, gpio=gpio, sleep=lambda _: None),
                      I2CTransport(bus=bus, gpio=Mock(), sleep=lambda _: None))
        config = DisplayConfig(rotation=270)
        for transport in transports:
            driver = SH1107(transport, config, sleep=lambda _: None)
            driver.open()
            for pattern in ('grid', 'checker', 'text', 'clear'):
                driver.show(image_to_frame(pattern_image(pattern, config), config))
            driver.set_contrast(32)
            driver.close()
        self.assertEqual(self.normalize(spi_events), self.normalize(i2c_events))
        # Initialization plus four full test frames: no page data lost at chunk boundaries.
        self.assertEqual(sum(len(p) for kind, p in i2c_events if kind == 1), 5 * 1024)


class I2CDiagnosticTests(unittest.TestCase):
    def test_offline_i2c_does_not_import_either_bus_library(self):
        with patch.dict(sys.modules, {'smbus': None, 'spidev': None, 'RPi.GPIO': None}), \
             patch.object(I2CTransport, 'open') as opened, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            main(['--interface', 'i2c', '--rotation', '270', '--pattern', 'grid'])
        opened.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual((report['interface'], report['hardware']), ('i2c', False))

    def test_same_offline_pattern_hash_on_both_interfaces(self):
        reports = []
        for interface in ('spi', 'i2c'):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                main(['--interface', interface, '--rotation', '270', '--pattern', 'all'])
            reports.append([json.loads(line)['sha256'] for line in output.getvalue().splitlines()])
        self.assertEqual(*reports)

    def test_cli_selects_i2c_and_releases_on_display_failure(self):
        driver = Mock()
        driver.show.side_effect = OSError('NACK')
        with patch('integrations.outputs.sh1107_test.I2CTransport') as transport, \
             patch('integrations.outputs.sh1107_test.SPITransport') as spi, \
             patch('integrations.outputs.sh1107_test.SH1107', return_value=driver), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
            main(['--interface', 'i2c', '--gpio', '--execute', '--rst-pin', '27',
                  '--i2c-bus', '3', '--i2c-address', '0x3D', '--pattern', 'grid'])
        transport.assert_called_once_with(I2CConfig(3, 0x3D, 27), gpio_warnings=False)
        spi.assert_not_called()
        driver.close.assert_called_once()

    def test_wrong_interface_options_and_missing_authorization_fail_before_open(self):
        for args in (
            ['--interface', 'i2c', '--dc-pin', '22'],
            ['--interface', 'i2c', '--spi-hz', '1000000'],
            ['--interface', 'i2c', '--spi-device', '0'],
            ['--i2c-bus', '1'], ['--i2c-address', '0x3c'],
            ['--interface', 'i2c', '--i2c-address', '0x78'],
            ['--interface', 'i2c', '--rst-pin', '2'],
            ['--interface', 'i2c', '--execute'],
        ):
            with self.subTest(args=args), \
                 patch.object(I2CTransport, 'open') as opened, \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args)
            opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
