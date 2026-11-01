"""Strict opt-in SSD1327 recovery budget.

Only explicit OSError from SPI operations can request reinitialization.
A write-only SPI controller offers no reliable display-on feedback.
"""
class RecoveryBudget:
    def __init__(self, attempts=1):
        if type(attempts) is not int or not 0 <= attempts <= 3:
            raise ValueError('recovery attempts must be 0..3')
        self.remaining = attempts

    def run(self, driver, frame, *, contrast=None):
        """Consume one attempt before I/O; never retry failed reinitialization."""
        if self.remaining == 0:
            return False
        self.remaining -= 1
        driver.recover_once(frame, contrast=contrast)
        return True
