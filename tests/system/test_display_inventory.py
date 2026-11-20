"""Offline, read-only tests for M4b display inventory."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from display_inventory import inventory_lines  # noqa: E402


class DisplayInventoryTests(unittest.TestCase):
    def test_missing_table_uses_actual_ssd1327_defaults(self):
        report = '\n'.join(inventory_lines({}))
        self.assertIn('controller: ssd1327 (runtime default)', report)
        self.assertIn('dc_pin: 25 (runtime default)', report)
        self.assertIn('rst_pin: 24 (runtime default)', report)
        self.assertIn('accepted by current runtime', report)

    def test_explicit_valid_pins(self):
        report = '\n'.join(inventory_lines({'display': {
            'controller': 'ssd1327', 'interface': 'spi', 'dc_pin': 22, 'rst_pin': 27}}))
        self.assertIn('dc_pin: 22 (configured)', report)
        self.assertIn('rst_pin: 27 (configured)', report)
        self.assertIn('accepted by current runtime', report)

    def test_experimental_controller_not_misrepresented_as_working(self):
        report = '\n'.join(inventory_lines({'display': {'controller': 'st7735s'}}))
        self.assertIn('NOT accepted by the current SSD1327 runtime', report)
        self.assertIn('not connected to player', report)
        self.assertIn('No display, pins, rotation', report)

    def test_invalid_pin_and_unknown_key_reported(self):
        self.assertIn('NOT accepted', '\n'.join(inventory_lines({'display': {'dc_pin': 8}})))
        self.assertIn('unknown display keys: rotation', '\n'.join(
            inventory_lines({'display': {'rotation': 270}})))

    def test_bad_table_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'TOML table'):
            inventory_lines({'display': 'ssd1327'})

    def test_detects_explicit_pin_collision(self):
        report = '\n'.join(inventory_lines(
            {'display': {'dc_pin': 22, 'rst_pin': 27},
             'led_outputs': {'power_pin': 12, 'connection_pin': 13}},
            {'buttons': {'volume_dn': 22, 'volume_up': 27},
             'encoder': {'encoder_a': 6, 'encoder_b': 26, 'encoder_sw': 5}}))
        self.assertIn('WARNING: BCM22: display.dc_pin, buttons.volume_dn', report)
        self.assertIn('WARNING: BCM27: display.rst_pin, buttons.volume_up', report)

    def test_interface_missing_does_not_claim_pins_free(self):
        report = '\n'.join(inventory_lines({'display': {'dc_pin': 22}}))
        self.assertIn('button/encoder collisions UNKNOWN', report)

    def test_no_collisions_when_explicitly_distinct(self):
        report = '\n'.join(inventory_lines(
            {'display': {'dc_pin': 22, 'rst_pin': 27}},
            {'buttons': {'playpause': 14}, 'encoder': {'encoder_a': 6}}))
        self.assertIn('No overlaps among the inspected configured pins.', report)

    def test_cli_with_interface_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            player = Path(directory) / 'player.toml'
            interface = Path(directory) / 'interface.toml'
            player.write_text('[display]\ndc_pin = 22\nrst_pin = 27\n')
            interface.write_text('[buttons]\nvolume_dn = 22\n')
            before = (player.read_bytes(), interface.read_bytes())
            result = subprocess.run([sys.executable, str(ROOT / 'tools/display_inventory.py'),
                                     '--config', str(player)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('WARNING: BCM22', result.stdout)
            self.assertEqual((player.read_bytes(), interface.read_bytes()), before)

    def test_tested_landscape_catalogue_geometry(self):
        report = '\n'.join(inventory_lines({}))
        self.assertIn('ST7735S, 160x128', report)
        self.assertIn('SH1107, 128x64', report)
        self.assertIn('not connected to player', report)

    def test_cli_read_only_and_no_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'player.toml'
            original = b'[streaming]\nmax_buffer_mib = 16\n[display]\ndc_pin = 22\nrst_pin = 27\n'
            path.write_bytes(original)
            result = subprocess.run([sys.executable, str(ROOT / 'tools/display_inventory.py'),
                                     '--config', str(path)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(path.read_bytes(), original)
            self.assertIn('read-only', result.stdout)


if __name__ == '__main__':
    unittest.main()
