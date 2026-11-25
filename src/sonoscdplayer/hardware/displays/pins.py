"""Display configuration shared by runtime, boot and shutdown tools."""
from dataclasses import dataclass
from pathlib import Path
import tomllib
from sonoscdplayer.hardware.displays.runtime import backend_for


@dataclass(frozen=True)
class DisplayPins:
    """Controller, bus, DC and RESET pins of the display (``[display]`` in player.toml)."""
    controller: str = 'ssd1327'
    interface: str = 'spi'
    spi_bus: int = 0
    spi_device: int = 0
    dc_pin: int = 25
    rst_pin: int = 24
    rotation: int = 0
    color_order: str = 'rgb'
    spi_hz: int = 2_000_000
    contrast: int = 128
    ambient_auto: bool = False
    adopt_boot_splash: bool = False
    preserve_on_exit: bool = False
    recover_on_error: bool = False

    def __post_init__(self):
        backend = backend_for(self.controller)
        if type(self.interface) is not str or self.interface != 'spi':
            raise ValueError('Unsupported display interface (configured OLED requires spi)')
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
        if type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270):
            raise ValueError('Display rotation must be 0, 90, 180 or 270')
        if self.color_order not in ('rgb', 'bgr'):
            raise ValueError('Display color_order must be rgb or bgr')
        if not backend.rotates and self.rotation != 0:
            raise ValueError('Configured backend currently requires rotation 0')
        if not backend.color and self.color_order != 'rgb':
            raise ValueError('Configured grayscale backend does not support BGR')
        if type(self.spi_hz) is not int or not 100_000 <= self.spi_hz <= 8_000_000:
            raise ValueError('Display spi_hz must be 100000..8000000')
        if type(self.contrast) is not int or not 0 <= self.contrast <= 255:
            raise ValueError('Display contrast must be 0..255')
        for key in ('ambient_auto', 'adopt_boot_splash', 'preserve_on_exit', 'recover_on_error'):
            if type(getattr(self, key)) is not bool:
                raise ValueError('Display ' + key + ' must be boolean')


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
