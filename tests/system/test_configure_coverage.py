"""M4b.17 read-only coverage and deliberate hardware boundaries."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'sonos_configure_coverage', ROOT / 'tools/configure_coverage.py')
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


class CoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = coverage.inventory()

    def test_all_three_configuration_files_are_inventoried(self):
        self.assertEqual(set(self.result),
                         {'player.toml', 'ui.toml', 'interface.toml'})

    def test_reference_keys_have_one_classification(self):
        examples = {'player.toml': 'player.example.toml',
                    'ui.toml': 'ui.example.toml',
                    'interface.toml': 'interface.example.toml'}
        for name, file in examples.items():
            with (ROOT / 'config' / file).open('rb') as stream:
                expected = coverage._paths(tomllib.load(stream))
            groups = self.result[name]
            classified = [set(groups[key]) for key in
                          ('editable', 'guarded', 'not_covered')]
            self.assertEqual(set.union(*classified), expected)
            for first in range(3):
                for second in range(first + 1, 3):
                    self.assertFalse(classified[first] & classified[second])

    def test_wizard_fields_have_reference_defaults(self):
        for name, groups in self.result.items():
            with self.subTest(config=name):
                self.assertEqual(groups['unreferenced'], ())

    def test_led_ambient_calibration_is_editable_but_sensor_transport_guarded(self):
        player = self.result['player.toml']
        self.assertIn('led_outputs.ambient.enabled', player['editable'])
        for field in ('poll_ms', 'stale_ms', 'smoothing_ms', 'timeout_ms',
                      'min_brightness', 'max_brightness',
                      'fallback_brightness', 'lux_dark', 'lux_bright'):
            self.assertIn('led_outputs.ambient.' + field,
                          player['editable'])
        self.assertIn('led_outputs.ambient.socket_path', player['guarded'])

    def test_led_pins_are_deliberately_guarded(self):
        player = self.result['player.toml']
        self.assertIn('led_outputs.power_pin', player['guarded'])
        self.assertIn('led_outputs.connection_pin', player['guarded'])

    def test_interface_pin_mapping_is_deliberately_guarded(self):
        interface = self.result['interface.toml']
        self.assertIn('buttons.power', interface['guarded'])
        self.assertIn('encoder.encoder_a', interface['guarded'])
        self.assertIn('encoder_input.invert_direction', interface['editable'])

    def test_player_and_ui_wizard_key_examples(self):
        self.assertIn('metadata.matching.rule_languages',
                      self.result['player.toml']['editable'])
        self.assertIn('ui.cover.mode', self.result['ui.toml']['editable'])
        self.assertIn('service.experimental_safe_screen',
                      self.result['player.toml']['guarded'])

    def test_display_runtime_is_still_ssd1327_only(self):
        from sonoscdplayer.hardware.displays.pins import DisplayPins
        self.assertEqual(DisplayPins().controller, 'ssd1327')
        with self.assertRaises(ValueError):
            DisplayPins(controller='st7735s')

    def test_reporting_does_not_write_configs(self):
        paths = [ROOT / 'config' / name for name in
                 ('player.example.toml', 'ui.example.toml',
                  'interface.example.toml')]
        before = [path.read_bytes() for path in paths]
        report = '\n'.join(coverage.report_lines())
        after = [path.read_bytes() for path in paths]
        self.assertEqual(before, after)
        self.assertIn('READ-ONLY', report)
        self.assertIn('Display selection: SSD1327 and SSD1351', report)


if __name__ == '__main__':
    unittest.main()
