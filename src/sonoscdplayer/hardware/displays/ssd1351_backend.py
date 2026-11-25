"""SSD1351 runtime adapter: shared contrast scale and bounded I/O recovery."""
from sonoscdplayer.hardware.displays.ssd1351 import SSD1351, native_bytes


def master_current(contrast):
    """Map 0..255 to C7 levels 0..15; zero remains minimum current, not OFF."""
    if type(contrast) is not int or not 0 <= contrast <= 255:
        raise ValueError('contrast must be an integer 0..255')
    return (contrast * 15 + 127) // 255


class SSD1351Backend(SSD1351):
    """Adapt SSD1351 current control and recovery to the shared runtime API."""
    def set_contrast(self, value):
        """Apply shared 0..255 contrast, suppressing duplicate native levels."""
        level = master_current(value)
        if not self.opened:
            raise RuntimeError('SSD1351 not opened')
        if level != getattr(self, '_last_current', self.config.brightness):
            self.set_brightness(level)

    def set_brightness(self, value):
        super().set_brightness(value)
        self._last_current = value

    def open(self, *, adopt_existing=False):
        if self.opened:
            return
        super().open(adopt_existing=adopt_existing)
        self._last_current = self.config.brightness

    def recover_once(self, frame, *, contrast=None):
        """Reinitialize and redraw after the caller consumes its recovery budget."""
        native_bytes(frame, self.config)  # Reject bad frames before touching I/O.
        self.close()
        self.open()
        if contrast is not None:
            self.set_contrast(contrast)
        self.show(frame)
