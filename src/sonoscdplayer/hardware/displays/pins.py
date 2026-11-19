"""Validated pin assignment shared by OLED runtime and build-time tools."""
from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class DisplayPins:
    controller: str = 'ssd1327'
    interface: str = 'spi'
    spi_bus: int = 0
    spi_device: int = 0
    dc_pin: int = 25
    rst_pin: int = 24

    def __post_init__(self):
        if type(self.controller) is not str or self.controller != 'ssd1327':
            raise ValueError('Unsupported display controller (supported: ssd1327)')
        if type(self.interface) is not str or self.interface != 'spi':
            raise ValueError('Unsupported display interface (ssd1327 currently requires spi)')
        if type(self.spi_bus) is not int or self.spi_bus != 0:
            raise ValueError('Only SPI0 is supported')
        if type(self.spi_device) is not int or self.spi_device != 0:
            raise ValueError('Only SPI0/CE0 is supported')
        if (type(self.dc_pin) is not int or type(self.rst_pin) is not int or
                not 0 <= self.dc_pin <= 27 or not 0 <= self.rst_pin <= 27):
            raise ValueError('Display pins must be BCM integers 0..27')
        if self.dc_pin == self.rst_pin:
            raise ValueError('Display DC and RESET pins must differ')
        if {self.dc_pin, self.rst_pin} & {7, 8, 9, 10, 11}:
            raise ValueError('Display DC/RESET must not overlap SPI0 pins')


def load_display_pins(path):
    """Read only [display] from TOML. Never modify the configuration."""
    with open(Path(path), 'rb') as stream:
        document = tomllib.load(stream)
    values = document.get('display', {})
    if not isinstance(values, dict):
        raise ValueError('[display] must be a table')
    allowed = set(DisplayPins.__dataclass_fields__)
    unknown = set(values) - allowed
    if unknown:
        raise ValueError('Unknown display setting: ' + ', '.join(sorted(unknown)))
    return DisplayPins(**values)
