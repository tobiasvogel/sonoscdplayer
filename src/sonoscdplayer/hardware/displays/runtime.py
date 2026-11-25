"""Explicit controller registry for the display service and artifact builders.

Only backends contain controller-specific construction and current commands.
Configuration selects a registry entry; no hardware is imported or opened here.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendSpec:
    """Construction and output capabilities of one explicitly registered backend."""
    controller: str
    pixel_format: str
    color: bool
    factory: object
    rotates: bool = False

    def create(self, config, *, gpio_warnings=False):
        """Construct a driver without opening display hardware."""
        return self.factory(config, gpio_warnings=gpio_warnings)


def _ssd1327(config, *, gpio_warnings):
    from sonoscdplayer.hardware.displays.ssd1327 import SSD1327, DisplayConfig
    return SSD1327(DisplayConfig(bus=config.spi_bus, device=config.spi_device,
        dc_pin=config.dc_pin, rst_pin=config.rst_pin,
        spi_hz=config.spi_hz, contrast=config.contrast), gpio_warnings=gpio_warnings)


def _ssd1351(config, *, gpio_warnings):
    from sonoscdplayer.hardware.displays.ssd1351 import DisplayConfig
    from sonoscdplayer.hardware.displays.transport import SPIConfig, SPITransport
    from sonoscdplayer.hardware.displays.ssd1351_backend import SSD1351Backend, master_current
    return SSD1351Backend(SPITransport(SPIConfig(bus=config.spi_bus,
        device=config.spi_device, dc_pin=config.dc_pin, rst_pin=config.rst_pin,
        spi_hz=config.spi_hz), gpio_warnings=gpio_warnings),
        DisplayConfig(rotation=config.rotation, color_order=config.color_order,
                      brightness=master_current(config.contrast)))


BACKENDS = {
    'ssd1327': BackendSpec('ssd1327', 'gray4-packed', False, _ssd1327),
    'ssd1351': BackendSpec('ssd1351', 'rgb565', True, _ssd1351, rotates=True),
}


def backend_for(controller):
    """Resolve a supported controller name, rejecting unregistered backends."""
    try:
        return BACKENDS[controller]
    except (KeyError, TypeError):
        raise ValueError('Unsupported display controller (supported: '
                         + ', '.join(BACKENDS) + ')') from None
