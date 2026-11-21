"""M4b.4 read-only display preparation, no hardware dependencies."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('display_preflight', ROOT / 'tools/display_preflight.py')
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class DisplayPreflightTests(unittest.TestCase):
    def test_non_runtime_displays_never_offered_as_ready(self):
        for key in ('st7735s', 'sh1107'):
            text = '\n'.join(preflight.preflight_lines({}, {}, key))
            self.assertIn('Result: BLOCKED', text)
            self.assertIn('no live player/UI', text)
            self.assertIn('rotation 270', text)

    def test_st7735s_gpio9_is_not_silently_approved(self):
        text = '\n'.join(preflight.preflight_lines({}, {}, 'st7735s', backlight_pin=9))
        self.assertIn('no_miso', text)
        self.assertIn('Result: BLOCKED', text)

    def test_backlight_collisions_detected(self):
        text = '\n'.join(preflight.preflight_lines(
            {'display': {'dc_pin': 22, 'rst_pin': 27}, 'led_outputs': {'power_pin': 12, 'connection_pin': 13}},
            {'buttons': {'volume_dn': 9}, 'encoder': {'encoder_a': 6, 'encoder_b': 26, 'encoder_sw': 5}},
            'st7735s', backlight_pin=9))
        self.assertIn('CONFLICT: BCM9 overlaps buttons.volume_dn', text)

    def test_missing_inputs_never_assumed_free(self):
        text = '\n'.join(preflight.preflight_lines({}, None, 'ssd1327'))
        self.assertIn('UNKNOWN: interface.toml unavailable', text)
        self.assertIn('Result: BLOCKED', text)

    def test_disabled_button_not_treated_as_gpio(self):
        text = '\n'.join(preflight.preflight_lines(
            {'display': {'dc_pin': 22, 'rst_pin': 27}},
            {'buttons': {'speaker_select': -1}, 'encoder': {'encoder_a': 6, 'encoder_b': 26, 'encoder_sw': 5}},
            'ssd1327'))
        self.assertNotIn('invalid BCM value for buttons.speaker_select', text)

    def test_cli_has_no_writes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)/'player.toml'
            cfg.write_text('[display]\ndc_pin = 22\nrst_pin = 27\n')
            original = cfg.read_bytes()
            result = subprocess.run([sys.executable, str(ROOT/'tools/display_preflight.py'),
                                     '--config', str(cfg), '--target', 'st7735s',
                                     '--backlight-pin', '9'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('BLOCKED', result.stdout)
            self.assertEqual(cfg.read_bytes(), original)

    def test_reject_unknown_target(self):
        with self.assertRaisesRegex(ValueError, 'unknown display candidate'):
            preflight.preflight_lines({}, {}, 'epaper')


if __name__ == '__main__':
    unittest.main()
