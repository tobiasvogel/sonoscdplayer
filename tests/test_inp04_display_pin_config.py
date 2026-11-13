"""INP-04 offline OLED configuration, splash and shutdown header contracts."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from integrations.outputs.display_pins import DisplayPins, load_display_pins
from integrations.outputs.ssd1327 import DisplayConfig
from tools.generate_ssd1327_display_header import render_header
from tools.build_ssd1327_splash import build


ROOT = Path(__file__).resolve().parents[1]


class DisplayPinConfigTests(unittest.TestCase):
    def test_original_default_and_new_board(self):
        self.assertEqual((DisplayPins().controller, DisplayPins().interface),
                         ('ssd1327', 'spi'))
        self.assertEqual((DisplayPins().dc_pin, DisplayPins().rst_pin), (25, 24))
        self.assertEqual((DisplayConfig().dc_pin, DisplayConfig().rst_pin), (25, 24))
        new = DisplayPins(dc_pin=22, rst_pin=27)
        self.assertEqual((new.dc_pin, new.rst_pin), (22, 27))
        self.assertEqual((DisplayConfig(dc_pin=22, rst_pin=27).dc_pin,
                          DisplayConfig(dc_pin=22, rst_pin=27).rst_pin), (22, 27))

    def test_toml_splash_and_header_share_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'local.toml'
            config.write_text('[display]\ncontroller = "ssd1327"\ninterface = "spi"\n'
                              'spi_bus = 0\nspi_device = 0\n'
                              'dc_pin = 22\nrst_pin = 27\n', encoding='utf-8')
            pins = load_display_pins(config)
            splash = build(bytes(8192), pins=pins)
            header = render_header(pins)
            self.assertIn('[dc 22]', splash)
            self.assertIn('RST27', splash)
            self.assertIn('#define SONOSCDPLAYER_OLED_DC_PIN 22u', header)
            self.assertIn('#define SONOSCDPLAYER_OLED_RST_PIN 27u', header)

    def test_unsupported_display_controllers_and_interfaces_fail_explicitly(self):
        for controller, interface in (('ssd1306', 'spi'), ('ssd1327', 'i2c'),
                                      ('ssd1327', 'I2C'), ('unknown', 'i2c')):
            with self.subTest(controller=controller, interface=interface):
                with self.assertRaisesRegex(ValueError, 'Unsupported display'):
                    DisplayPins(controller=controller, interface=interface)

    def test_toml_rejects_unimplemented_i2c_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'display.toml'
            config.write_text('[display]\ncontroller = "ssd1327"\n'
                              'interface = "i2c"\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Unsupported display interface'):
                load_display_pins(config)

    def test_invalid_pins_rejected(self):
        for dc, rst in ((22, 22), (8, 27), (22, 11), (True, 27),
                        (28, 27), (22, -1)):
            with self.subTest(dc=dc, rst=rst):
                with self.assertRaises(ValueError):
                    DisplayPins(dc_pin=dc, rst_pin=rst)

    def test_build_requires_explicit_local_display_config(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ['sh', str(ROOT / 'tools/build_shutdown_renderer.sh')],
                env={'PATH': '/usr/bin:/bin', 'BUILD_DIR': directory},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('DISPLAY_CONFIG', result.stderr)

    def test_build_replaces_stale_header_from_local_config(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            config = build / 'local.toml'
            header = build / 'ssd1327_display_config.h'
            config.write_text('[display]\ndc_pin = 22\nrst_pin = 27\n',
                              encoding='utf-8')
            header.write_text('#define SONOSCDPLAYER_OLED_DC_PIN 25u\n',
                              encoding='ascii')
            import os
            result = subprocess.run(
                ['sh', str(ROOT / 'tools/build_shutdown_renderer.sh')],
                env={**os.environ, 'BUILD_DIR': directory,
                     'DISPLAY_CONFIG': str(config)},
                capture_output=True, text=True, timeout=40,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            generated = header.read_text(encoding='ascii')
            self.assertIn('SONOSCDPLAYER_OLED_DC_PIN 22u', generated)
            self.assertIn('SONOSCDPLAYER_OLED_RST_PIN 27u', generated)
            self.assertNotIn('SONOSCDPLAYER_OLED_DC_PIN 25u', generated)

    def test_missing_config_does_not_reuse_stale_header(self):
        with tempfile.TemporaryDirectory() as directory:
            header = Path(directory) / 'ssd1327_display_config.h'
            header.write_text('#define SONOSCDPLAYER_OLED_DC_PIN 25u\n',
                              encoding='ascii')
            result = subprocess.run(
                ['sh', str(ROOT / 'tools/build_shutdown_renderer.sh')],
                env={'PATH': '/usr/bin:/bin', 'BUILD_DIR': directory},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('DISPLAY_CONFIG', result.stderr)

    def test_generator_cli_writes_expected_pin_constants(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'local.toml'
            header = Path(directory) / 'ssd1327_display_config.h'
            config.write_text('[display]\ndc_pin = 22\nrst_pin = 27\n',
                              encoding='utf-8')
            result = subprocess.run(
                [sys.executable, str(ROOT / 'tools/generate_ssd1327_display_header.py'),
                 '--config', str(config), '--output', str(header)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            source = header.read_text(encoding='ascii')
            self.assertIn('SONOSCDPLAYER_OLED_DC_PIN 22u', source)
            self.assertIn('SONOSCDPLAYER_OLED_RST_PIN 27u', source)


if __name__ == '__main__':
    unittest.main()
