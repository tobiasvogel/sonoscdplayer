"""Offline checks for bounded opt-in display recovery."""
import unittest
from unittest.mock import Mock
from integrations.outputs.oled_recovery import RecoveryBudget
from integrations.outputs.ssd1327 import Framebuffer, SSD1327, DisplayConfig


class RecoveryTests(unittest.TestCase):
    def test_one_attempt_only(self):
        driver=Mock()
        frame=Framebuffer()
        budget=RecoveryBudget(1)
        self.assertTrue(budget.run(driver,frame,contrast=83))
        self.assertFalse(budget.run(driver,frame,contrast=83))
        driver.recover_once.assert_called_once_with(frame,contrast=83)

    def test_failure_consumes_budget(self):
        driver=Mock()
        driver.recover_once.side_effect=OSError('spi failed')
        budget=RecoveryBudget(1)
        with self.assertRaises(OSError):
            budget.run(driver,Framebuffer())
        self.assertFalse(budget.run(driver,Framebuffer()))

    def test_recovery_disabled(self):
        driver=Mock()
        self.assertFalse(RecoveryBudget(0).run(driver,Framebuffer()))
        driver.recover_once.assert_not_called()

    def test_invalid_budget(self):
        for attempts in (-1,4,True):
            with self.subTest(attempts=attempts),self.assertRaises(ValueError):
                RecoveryBudget(attempts)

    def test_driver_requires_valid_frame_before_touching_hardware(self):
        driver=SSD1327(DisplayConfig(),spi=Mock(),gpio=Mock())
        with self.assertRaises(ValueError):
            driver.recover_once(None)
        driver.spi.close.assert_not_called()


if __name__=='__main__':
    unittest.main()
