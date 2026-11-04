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

    def test_service_restart_adopts_preserved_frame_without_reset(self):
        first, first_spi, first_gpio = self.new_opened_driver()
        frame = Framebuffer()
        frame.pixel(0, 0, 15)
        first.show(frame)
        self.assertIn((0x5c,), first_spi.commands)
        first.close(blank=False)
        self.assertNotIn((0xae,), first_spi.commands)
        self.assertTrue(first_spi.closed)
        self.assertEqual(first_gpio.cleaned, [(25, 24)])

        # A restarted service takes over with a new SPI/GPIO file descriptor,
        # without a hardware reset or an OLED DISPLAY OFF command.
        second_spi, second_gpio = FakeSPI(), FakeGPIO()
        second = SSD1327(DisplayConfig(), spi=second_spi,
                         gpio=second_gpio, sleep=lambda _: None)
        second.open(adopt_existing=True)
        self.assertEqual(second_spi.commands, [(0xaf,)])
        self.assertNotIn((24, second_gpio.LOW), second_gpio.outputs)
        second.show(frame)
        self.assertIn((0x5c,), second_spi.commands)
        second.close()
        self.assertIn((0xae,), second_spi.commands)
        self.assertTrue(second_spi.closed)

    def test_normal_close_remains_default_after_preserved_handover(self):
        first, spi, _gpio = self.new_opened_driver()
        first.close(blank=False)
        second, second_spi, _second_gpio = self.new_opened_driver()
        second.close()
        self.assertEqual(spi.commands, [])
        self.assertEqual(second_spi.commands, [(0xae,)])

    def test_recovery_still_blanks_and_reinitializes(self):
        driver, spi, gpio = self.new_opened_driver()
        driver.recover_once(Framebuffer())
        self.assertIn((0xae,), spi.commands)
        self.assertTrue(driver.opened)


if __name__ == '__main__':
    unittest.main()
