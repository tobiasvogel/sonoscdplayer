"""INP-04: configurable PWM LED pin ownership, including early boot."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from integrations.outputs.led_pwm import OutputConfig, load_config
from tools.early_power_led import configured_power_pin


class LedPinMappingTests(unittest.TestCase):
    def test_default_and_swapped_mapping(self):
        default = OutputConfig()
        swapped = OutputConfig(power_pin=13, connection_pin=12)
        self.assertEqual((default.power_pin, default.connection_pin), (12, 13))
        self.assertEqual((swapped.power_pin, swapped.connection_pin), (13, 12))

    def test_invalid_and_duplicate_pins_rejected(self):
        for power, connection in ((12, 12), (13, 13), (19, 20),
                                  (12, 19), (True, 13), (12, False)):
            with self.subTest(power=power, connection=connection):
                with self.assertRaises(ValueError):
                    OutputConfig(power_pin=power, connection_pin=connection)

    def test_same_toml_mapping_for_regular_and_early_boot(self):
        for power, connection in ((12, 13), (13, 12)):
            with self.subTest(power=power):
                with TemporaryDirectory() as directory:
                    path = Path(directory) / 'player.toml'
                    path.write_text(
                        f'[led_outputs]\npower_pin = {power}\nconnection_pin = {connection}\n',
                        encoding='utf-8',
                    )
                    self.assertEqual(load_config(str(path)).power_pin, power)
                    self.assertEqual(configured_power_pin(path), power)

    def test_early_boot_rejects_invalid_mapping(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'player.toml'
            path.write_text('[led_outputs]\npower_pin = 12\nconnection_pin = 12\n',
                            encoding='utf-8')
            with self.assertRaises(ValueError):
                configured_power_pin(path)


if __name__ == '__main__':
    unittest.main()
