"""M4b.12 LED wizard offline validation and service selection."""
import importlib.util
from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('configure_leds_test', ROOT / 'tools/configure.py')
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


class LEDWizardTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / 'config/player.example.toml').read_text()
        self.parsed = tomllib.loads(self.raw)
        self.reference = tomllib.loads(self.raw)
        self.current = config.existing_values(self.parsed, self.reference)

    def test_led_group_and_ranges(self):
        self.assertEqual(len(config.LED_MENU_GROUP[1]), 6)
        self.assertEqual(self.current[('led_outputs', 'brightness')], 0.7)
        self.assertEqual(config.ask('brightness', 0.7, 0.7, 'float01',
                                    read=lambda _: '0.25'), 0.25)

    def test_led_only_restarts_led_service(self):
        path, updated, _, services = config.plan_update(
            {('led_outputs', 'brightness'): 0.25}, self.current, {},
            player_path=ROOT/'player.toml', ui_path=ROOT/'ui.toml',
            raw=self.raw, ui_raw=None)
        self.assertEqual(services, ['sonoscdplayer-led.service'])
        self.assertAlmostEqual(tomllib.loads(updated)['led_outputs']['brightness'], 0.25)

    def test_led_and_player_restart_both(self):
        _, _, _, services = config.plan_update(
            {('led_outputs', 'brightness'): 0.3, ('sonos', 'use_queue'): False},
            self.current, {}, player_path=ROOT/'player.toml', ui_path=ROOT/'ui.toml',
            raw=self.raw, ui_raw=None)
        self.assertEqual(services, ['sonoscdplayer-player.service', 'sonoscdplayer-led.service'])

    def test_led_pin_reassignment_not_in_editor(self):
        self.assertNotIn(('led_outputs', 'power_pin'), config.LED_MENU_GROUP[1])

    def test_ambient_input_is_validated_as_boolean(self):
        staged = config.replace_values(self.raw, {('led_outputs.ambient', 'enabled'): False})
        self.assertIs(tomllib.loads(staged)['led_outputs']['ambient']['enabled'], False)

if __name__ == '__main__':
    unittest.main()
