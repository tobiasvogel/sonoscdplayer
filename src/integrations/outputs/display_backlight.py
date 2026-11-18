"""Explicit GPIO backlight ownership for the standalone LCD diagnostic."""
from dataclasses import dataclass
from typing import Protocol


class Backlight(Protocol):
    def open(self) -> None: ...
    def set(self, enabled: bool) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class BacklightConfig:
    pin: int
    spi_miso_released: bool = False

    def __post_init__(self):
        if type(self.pin) is not int or not 0 <= self.pin <= 27:
            raise ValueError('backlight pin must be a BCM integer 0..27')
        if type(self.spi_miso_released) is not bool:
            raise ValueError('spi_miso_released must be boolean')
        if self.pin in (7, 8, 10, 11):
            raise ValueError('backlight must not overlap SPI0 pins')
        if self.pin == 9 and not self.spi_miso_released:
            raise ValueError('GPIO9 requires --spi-miso-released and a no_miso boot overlay')

    def validate_control_pins(self, spi_config):
        if self.pin in (spi_config.dc_pin, spi_config.rst_pin):
            raise ValueError('backlight must differ from DC and RESET')


class GPIOBacklight:
    """Waveshare BL input: active high; on/off only, no PWM in this increment."""
    def __init__(self, config: BacklightConfig, *, gpio=None, gpio_warnings=False):
        self.config = config
        self.gpio = gpio
        self.gpio_warnings = gpio_warnings
        self.opened = False
        self._owned = False

    def open(self):
        if self.opened:
            return
        try:
            if self.gpio is None:
                import RPi.GPIO as GPIO
                self.gpio = GPIO
            self.gpio.setwarnings(bool(self.gpio_warnings))
            self.gpio.setmode(self.gpio.BCM)
            if self.config.pin == 9 and self.gpio.gpio_function(9) != self.gpio.IN:
                raise ValueError('GPIO9 is not a free input; configure no_miso and reboot first')
            self._owned = True
            self.gpio.setup(self.config.pin, self.gpio.OUT, initial=self.gpio.LOW)
            self.opened = True
        except BaseException:
            try:
                self.close()
            except Exception:
                pass
            raise

    def set(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('backlight state must be boolean')
        if not self.opened:
            raise RuntimeError('backlight not opened')
        self.gpio.output(self.config.pin, self.gpio.HIGH if enabled else self.gpio.LOW)

    def close(self):
        self.opened = False
        if self._owned:
            self._owned = False
            try:
                self.gpio.output(self.config.pin, self.gpio.LOW)
            finally:
                self.gpio.cleanup([self.config.pin])
