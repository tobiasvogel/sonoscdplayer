"""Offline SSD1327 exit-handover regression tests (fake SPI/GPIO only)."""
import unittest

from integrations.outputs.ssd1327 import SSD1327, DisplayConfig, Framebuffer


class FakeSPI:
    def __init__(self):
        self.commands = []
        self.closed = False
        self.max_speed_hz = None
        self.mode = None

    def writebytes(self, values):
        self.commands.append(tuple(values))

    def close(self):
        self.closed = True


class FakeGPIO:
    BCM = 11
    OUT = 1
    LOW = 0
    HIGH = 1

    def __init__(self):
        self.cleaned = []
        self.outputs = []

    def setmode(self, mode):
        pass

    def setup(self, pin, mode, initial=None):
        pass

    def output(self, pin, level):
        self.outputs.append((pin, level))

    def cleanup(self, pins):
        self.cleaned.append(pins)


class ExitHandoverTests(unittest.TestCase):
    def new_opened_driver(self):
        spi, gpio = FakeSPI(), FakeGPIO()
        driver = SSD1327(DisplayConfig(), spi=spi, gpio=gpio, sleep=lambda _: None)
        driver.open(adopt_existing=True)
        self.assertIn((0xaf,), spi.commands)
        spi.commands.clear()
        gpio.outputs.clear()
        return driver, spi, gpio

    def test_default_close_turns_display_off(self):
        driver, spi, gpio = self.new_opened_driver()
        driver.close()
        self.assertEqual(spi.commands, [(0xae,)])
        self.assertTrue(spi.closed)
        self.assertEqual(gpio.cleaned, [(25, 24)])
        self.assertFalse(driver.opened)

    def test_preserving_close_keeps_display_on_and_releases_resources(self):
        driver, spi, gpio = self.new_opened_driver()
        driver.close(blank=False)
        self.assertEqual(spi.commands, [])
        self.assertEqual(gpio.outputs, [])
        self.assertTrue(spi.closed)
        self.assertEqual(gpio.cleaned, [(25, 24)])
        self.assertFalse(driver.opened)

    def test_recovery_still_blanks_and_reinitializes(self):
        driver, spi, gpio = self.new_opened_driver()
        driver.recover_once(Framebuffer())
        self.assertIn((0xae,), spi.commands)
        self.assertTrue(driver.opened)


if __name__ == '__main__':
    unittest.main()
