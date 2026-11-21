"""M4b.18 offline APDS9306 wizard fields and cross-field safety."""
import importlib.util
from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('sonos_configure_ambient', ROOT/'tools/configure.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class AmbientWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (ROOT/'config/player.example.toml').read_text(encoding='utf-8')
        cls.reference = tomllib.loads(cls.raw)
        cls.current = config.existing_values(cls.reference, cls.reference)

    def plan(self, changes):
        return config.plan_update(changes, self.current, {},
                                  player_path=ROOT/'player.toml', ui_path=ROOT/'ui.toml',
                                  raw=self.raw, ui_raw=None)

    def test_all_nine_ambient_fields_in_led_menu(self):
        fields = set(config.LED_MENU_GROUP[1])
        for name in ('timeout_ms', 'poll_ms', 'stale_ms', 'smoothing_ms',
                     'min_brightness', 'max_brightness', 'fallback_brightness',
                     'lux_dark', 'lux_bright'):
            self.assertIn(('led_outputs.ambient', name), fields)
        self.assertNotIn(('led_outputs.ambient', 'socket_path'), fields)
        self.assertNotIn(('led_outputs.ambient', 'request'), fields)

    def test_existing_example_values_are_accepted(self):
        self.assertEqual(self.current[('led_outputs.ambient', 'poll_ms')], 1000)
        self.assertEqual(self.current[('led_outputs.ambient', 'lux_bright')], 100.0)

    def test_ambient_only_restarts_led_service(self):
        _, result, _, services = self.plan({('led_outputs.ambient', 'poll_ms'): 1200})
        self.assertEqual(services, ['sonoscdplayer-led.service'])
        self.assertEqual(tomllib.loads(result)['led_outputs']['ambient']['poll_ms'], 1200)
        self.assertEqual(tomllib.loads(result)['led_outputs']['ambient']['socket_path'],
                         '/run/apds9306.sock')

    def test_valid_combined_calibration(self):
        _, result, _, services = self.plan({
            ('led_outputs.ambient', 'poll_ms'): 2000,
            ('led_outputs.ambient', 'stale_ms'): 9000,
            ('led_outputs.ambient', 'min_brightness'): 0.10,
            ('led_outputs.ambient', 'max_brightness'): 0.90,
            ('led_outputs.ambient', 'lux_dark'): 0.5,
            ('led_outputs.ambient', 'lux_bright'): 500.0,
        })
        self.assertEqual(services, ['sonoscdplayer-led.service'])
        ambient = tomllib.loads(result)['led_outputs']['ambient']
        self.assertEqual(ambient['lux_bright'], 500.0)
        self.assertEqual(ambient['stale_ms'], 9000)

    def test_stale_poll_dependency_blocks_invalid_combination(self):
        with self.assertRaises(ValueError):
            self.plan({('led_outputs.ambient', 'poll_ms'): 6000})

    def test_brightness_order_is_validated(self):
        with self.assertRaises(ValueError):
            self.plan({('led_outputs.ambient', 'min_brightness'): 0.95})

    def test_lux_order_is_validated(self):
        with self.assertRaises(ValueError):
            self.plan({('led_outputs.ambient', 'lux_dark'): 101.0})

    def test_numeric_range_rejected_by_runtime(self):
        with self.assertRaises(ValueError):
            self.plan({('led_outputs.ambient', 'timeout_ms'): 2})

    def test_lux_input_rejects_nonnumeric_negative_nonfinite(self):
        replies = iter(['-1', 'NaN', 'Inf', '1e1000', '12.5'])
        logs = []
        self.assertEqual(config.ask('Lux', 1.0, 1.0, 'float_lux',
                                    read=lambda _: next(replies), out=logs.append), 12.5)
        self.assertEqual(len(logs), 4)

    def test_existing_invalid_lux_is_rejected(self):
        doc = tomllib.loads(self.raw)
        doc['led_outputs']['ambient']['lux_dark'] = -2.0
        with self.assertRaises(ValueError):
            config.existing_values(doc, self.reference)

    def test_ambient_and_other_player_changes_restart_both(self):
        _, _, _, services = self.plan({('led_outputs.ambient', 'poll_ms'): 1200,
                                      ('sonos', 'use_queue'): False})
        self.assertEqual(services, ['sonoscdplayer-player.service',
                                    'sonoscdplayer-led.service'])


if __name__ == '__main__':
    unittest.main()
