"""SPI and I2C transport for display controllers.

A controller driver (SH1107, ST7735S) sends commands and data through a
DisplayTransport and does not care about the bus. Importing this module
opens no hardware; ``open()`` does.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import time


class DisplayTransport(Protocol):
    """What a controller driver needs from the bus."""

    def open(self) -> None:
        """Open the bus and the control pins."""
        ...

    def reset(self) -> None:
        """Pulse the display's RESET pin."""
        ...

    def command(self, payload: bytes) -> None:
        """Send command bytes."""
        ...

    def data(self, payload: bytes) -> None:
        """Send display data bytes."""
        ...

    def close(self) -> None:
        """Release the bus and the pins."""
        ...


@dataclass(frozen=True)
class I2CConfig:
    """I2C bus, address and optional RESET pin."""
    bus: int = 1
    address: int = 0x3C
    rst_pin: int = 24

    def __post_init__(self):
        if type(self.bus) is not int or not 0 <= self.bus <= 255:
            raise ValueError('I2C bus must be an integer 0..255')
        if type(self.address) is not int or self.address not in (0x3C, 0x3D):
            raise ValueError('SH1107 I2C address must be 0x3C or 0x3D (7-bit)')
        if type(self.rst_pin) is not int or not 0 <= self.rst_pin <= 27:
            raise ValueError('RESET must be a BCM integer 0..27')
        if self.rst_pin in (2, 3):
            raise ValueError('RESET must not overlap I2C1 SDA/SCL pins')
        if self.bus == 0 and self.rst_pin in (0, 1):
            raise ValueError('RESET must not overlap I2C0 SDA/SCL pins')


class I2CTransport:
    """SH1107 I2C control bytes over Linux SMBus I2C-block writes.

    write_i2c_block_data sends control + payload without an SMBus length byte.
    Each transaction carries at most 32 payload bytes, preceded by 0x00 for
    commands or 0x40 for RAM data. Only RESET is driven as a GPIO: address
    selection (DC) and chip select (CS) are wired to fixed levels.
    """
    def __init__(self, config=I2CConfig(), *, bus=None, gpio=None,
                 sleep=time.sleep, gpio_warnings=False):
        self.config = config
        self.bus = bus
        self.gpio = gpio
        self.sleep = sleep
        self.gpio_warnings = gpio_warnings
        self.opened = False
        self._bus_open = False
        self._pin_owned = False

    def open(self):
        """Open the I2C bus and the RESET pin."""
        if self.opened:
            return
        try:
            if self.gpio is None:
                import RPi.GPIO as GPIO
                self.gpio = GPIO
            if self.bus is None:
                import smbus
                self.bus = smbus.SMBus()
            self._bus_open = True
            self.bus.open(self.config.bus)
            self.gpio.setwarnings(bool(self.gpio_warnings))
            self.gpio.setmode(self.gpio.BCM)
            self._pin_owned = True
            self.gpio.setup(self.config.rst_pin, self.gpio.OUT, initial=self.gpio.HIGH)
            self.opened = True
        except BaseException:
            try:
                self.close()
            except Exception:
                pass
            raise

    def _require_open(self):
        if not self.opened:
            raise RuntimeError('transport not opened')

    def reset(self):
        """Pulse RESET (if a pin is configured)."""
        self._require_open()
        self.gpio.output(self.config.rst_pin, self.gpio.HIGH)
        self.sleep(.001)
        self.gpio.output(self.config.rst_pin, self.gpio.LOW)
        self.sleep(.02)
        self.gpio.output(self.config.rst_pin, self.gpio.HIGH)
        self.sleep(.02)

    def _write(self, payload, control):
        self._require_open()
        if not isinstance(payload, bytes):
            raise TypeError('transport payload must be bytes')
        for offset in range(0, len(payload), 32):
            self.bus.write_i2c_block_data(
                self.config.address, control, list(payload[offset:offset + 32]))

    def command(self, payload):
        """Send command bytes (control byte 0x00)."""
        self._write(payload, 0x00)

    def data(self, payload):
        """Send data bytes (control byte 0x40)."""
        self._write(payload, 0x40)

    def close(self):
        """Release bus and pin."""
        self.opened = False
        try:
            if self._bus_open:
                self._bus_open = False
                self.bus.close()
        finally:
            if self._pin_owned:
                self._pin_owned = False
                self.gpio.cleanup([self.config.rst_pin])


@dataclass(frozen=True)
class SPIConfig:
    """SPI bus/device, speed and the DC and RESET pins."""
    bus: int = 0
    device: int = 0
    dc_pin: int = 25
    rst_pin: int = 24
    spi_hz: int = 2_000_000

    def __post_init__(self):
        if type(self.bus) is not int or self.bus != 0:
            raise ValueError('only SPI0 is supported')
        if type(self.device) is not int or self.device not in (0, 1):
            raise ValueError('SPI device must be CE0 or CE1')
        for pin in (self.dc_pin, self.rst_pin):
            if type(pin) is not int or not 0 <= pin <= 27:
                raise ValueError('pins must be BCM integers 0..27')
            if pin in (7, 8, 9, 10, 11):
                raise ValueError('control pins must not overlap SPI0')
        if self.dc_pin == self.rst_pin:
            raise ValueError('DC and RESET must differ')
        if type(self.spi_hz) is not int or not 100_000 <= self.spi_hz <= 8_000_000:
            raise ValueError('SPI clock must be 100000..8000000 Hz')


class SPITransport:
    """Hardware CE selection, SPI mode 0, and only the two owned GPIO pins.

    Injected SPI/GPIO objects follow the same lifecycle as physical ones.
    I2C can implement DisplayTransport without changing the controller.
    """
    def __init__(self, config=SPIConfig(), *, spi=None, gpio=None,
                 sleep=time.sleep, gpio_warnings=False):
        self.config = config
        self.spi = spi
        self.gpio = gpio
        self.sleep = sleep
        self.gpio_warnings = gpio_warnings
        self.opened = False
        self._pins = []
        self._spi_open = False

    def open(self):
        """Open SPI and the DC/RESET pins."""
        if self.opened:
            return
        try:
            if self.gpio is None:
                import RPi.GPIO as GPIO
                self.gpio = GPIO
            if self.spi is None:
                import spidev
                self.spi = spidev.SpiDev()
            # Record ownership before open: even a partially failed open is closed.
            self._spi_open = True
            self.spi.open(self.config.bus, self.config.device)
            self.spi.max_speed_hz = self.config.spi_hz
            self.spi.mode = 0
            self.gpio.setwarnings(bool(self.gpio_warnings))
            self.gpio.setmode(self.gpio.BCM)
            for pin, initial in ((self.config.dc_pin, self.gpio.LOW),
                                 (self.config.rst_pin, self.gpio.HIGH)):
                self._pins.append(pin)
                self.gpio.setup(pin, self.gpio.OUT, initial=initial)
            self.opened = True
        except BaseException:
            try:
                self.close()
            except Exception:
                pass  # Preserve the original initialization failure.
            raise

    def _require_open(self):
        if not self.opened:
            raise RuntimeError('transport not opened')

    def reset(self):
        """Pulse RESET."""
        self._require_open()
        self.gpio.output(self.config.rst_pin, self.gpio.HIGH)
        self.sleep(.001)
        self.gpio.output(self.config.rst_pin, self.gpio.LOW)
        self.sleep(.02)
        self.gpio.output(self.config.rst_pin, self.gpio.HIGH)
        self.sleep(.02)

    def _write(self, payload, level):
        self._require_open()
        if not isinstance(payload, bytes):
            raise TypeError('transport payload must be bytes')
        self.gpio.output(self.config.dc_pin, level)
        for offset in range(0, len(payload), 4096):
            self.spi.writebytes(list(payload[offset:offset + 4096]))

    def command(self, payload):
        """Send command bytes (DC low)."""
        self._require_open()
        self._write(payload, self.gpio.LOW)

    def data(self, payload):
        """Send data bytes (DC high)."""
        self._require_open()
        self._write(payload, self.gpio.HIGH)

    def close(self):
        """Release SPI and the pins."""
        self.opened = False
        try:
            if self._spi_open:
                self._spi_open = False
                self.spi.close()
        finally:
            if self._pins:
                pins, self._pins = self._pins, []
                self.gpio.cleanup(pins)
