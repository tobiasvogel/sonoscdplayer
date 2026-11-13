"""[gpio].warnings in player.toml controls RPi.GPIO warnings everywhere."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gpio_settings import apply_gpio_warnings, gpio_warnings_enabled
from integrations.outputs.led_pwm import OutputConfig, PwmOutputs
from integrations.outputs.ssd1327 import DisplayConfig, SSD1327
from tools import early_power_led

ROOT = Path(__file__).resolve().parents[1]


class FakeGPIO:
    BCM = 'BCM'; OUT = 'OUT'; IN = 'IN'; LOW = 0; HIGH = 1

    def __init__(self):
        self.calls = []

    def setwarnings(self, value):
        self.calls.append(('setwarnings', value))

    def setmode(self, mode):
        self.calls.append(('setmode', mode))

    def setup(self, *args, **kwargs):
        self.calls.append(('setup', args[0]))

    def output(self, *args):
        pass

    def cleanup(self, *args):
        pass

    def PWM(self, pin, hz):
        return mock.Mock()


class GpioWarningsTests(unittest.TestCase):
    def write(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'player.toml'
        path.write_text(text)
        return path

    def test_default_is_off_and_example_matches(self):
        self.assertFalse(gpio_warnings_enabled(self.write('')))
        self.assertFalse(gpio_warnings_enabled(ROOT / 'config/player.example.toml'))
        self.assertFalse(gpio_warnings_enabled(Path('/nonexistent/player.toml')))
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {'SONOSCDPLAYER_CONFIG_DIR': directory}):
            self.assertFalse(gpio_warnings_enabled())
            (Path(directory) / 'player.toml').write_text('[gpio]\nwarnings = true\n')
            self.assertTrue(gpio_warnings_enabled())

    def test_invalid_values_are_rejected(self):
        for text in ('gpio = 1\n', '[gpio]\nwarnings = "yes"\n', '[gpio]\nwarnings = 1\n'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    gpio_warnings_enabled(self.write(text))

    def test_apply_tolerates_backends_without_setwarnings(self):
        apply_gpio_warnings(object(), True)
        gpio = FakeGPIO()
        apply_gpio_warnings(gpio, True)
        self.assertEqual(gpio.calls, [('setwarnings', True)])

    def test_led_outputs_set_warnings_before_claiming_pins(self):
        for enabled in (False, True):
            gpio = FakeGPIO()
            PwmOutputs(OutputConfig(), gpio, gpio_warnings=enabled).open()
            self.assertEqual(gpio.calls[0], ('setwarnings', enabled))

    def test_oled_sets_warnings_before_claiming_pins(self):
        gpio = FakeGPIO()
        driver = SSD1327(DisplayConfig(), spi=mock.Mock(), gpio=gpio, sleep=lambda _s: None,
                         gpio_warnings=True)
        driver.open(adopt_existing=True)
        self.assertEqual(gpio.calls[0], ('setwarnings', True))

    def test_early_led_reads_pin_and_warnings_from_one_file(self):
        path = self.write('[gpio]\nwarnings = true\n[led_outputs]\npower_pin = 13\nconnection_pin = 12\n')
        self.assertEqual(early_power_led.configured_settings(path), (13, True))
        self.assertEqual(early_power_led.configured_power_pin(path), 13)
        with self.assertRaises(ValueError):
            early_power_led.configured_settings(self.write('[gpio]\nwarnings = "no"\n'))


if __name__ == '__main__':
    unittest.main()
