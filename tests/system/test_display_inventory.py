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
