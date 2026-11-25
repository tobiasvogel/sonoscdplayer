"""Standalone Waveshare 14747 SSD1351 128x128 RGB OLED driver.

RGB565 frames, software rotation and 16-step master current control. Hardware
I/O is injected through the shared SPI transport. No luma or backlight GPIO.
Register settings and hardware verification are documented in docs/SSD1351.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from sonoscdplayer.hardware.displays import rgb565
from sonoscdplayer.hardware.displays.transport import DisplayTransport
from sonoscdplayer.ui.displays.capabilities import DisplayCapabilities

WIDTH = HEIGHT = 128
FRAME_BYTES = 32768
CAPABILITIES = DisplayCapabilities(kind='graphic', width=128, height=128,
                                   pixel_format='rgb565')


@dataclass(frozen=True)
class DisplayConfig:
    """Validated rotation, color order and native master-current setting."""
    rotation: int = 0
    color_order: str = 'rgb'
    brightness: int = 8

    def __post_init__(self):
        if type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270):
            raise ValueError('rotation must be 0, 90, 180 or 270')
        if self.color_order not in ('rgb', 'bgr'):
            raise ValueError('color order must be rgb or bgr')
        if type(self.brightness) is not int or not 0 <= self.brightness <= 15:
            raise ValueError('brightness must be an integer 0..15')

    @property
    def size(self):
        return (128, 128)


def image_to_frame(image, config=DisplayConfig()):
    return rgb565.image_to_frame(image, config.size)


def native_bytes(frame, config=DisplayConfig()):
    return rgb565.native_bytes(frame, native_size=(128, 128), rotation=config.rotation)


def initialization_commands(config=DisplayConfig()):
    """Command/parameter vectors shared with the firmware splash generator."""
    return ((0xFD, b'\x12'), (0xFD, b'\xb1'), (0xAE, b''),
                (0xB3, b'\xf1'), (0xCA, b'\x7f'),
                (0xA0, bytes([0x74 if config.color_order == 'rgb' else 0x70])),
                (0xA1, b'\x00'), (0xA2, b'\x00'),
                (0xB5, b'\x00'), (0xAB, b'\x01'),
                (0xB1, b'\x32'), (0xBE, b'\x05'), (0xA6, b''),
                (0xC1, b'\xc8\x80\xc8'), (0xC7, bytes([config.brightness])),
                (0xB4, b'\xa0\xb5\x55'), (0xB6, b'\x01'))


class SSD1351:
    """128x128 RGB OLED driver with injected SPI transport and optional handover."""
    capabilities = CAPABILITIES

    def __init__(self, transport: DisplayTransport, config=DisplayConfig(), *, sleep=time.sleep):
        self.transport, self.config, self.sleep = transport, config, sleep
        self.opened = False
        self._transport_open = False

    def _cmd(self, command, parameters=b''):
        self.transport.command(bytes([command]))
        if parameters:
            self.transport.data(bytes(parameters))

    def _show_native(self, data):
        self._cmd(0x15, b'\x00\x7f')
        self._cmd(0x75, b'\x00\x7f')
        self._cmd(0x5C)
        self.transport.data(data)

    def open(self, *, adopt_existing=False):
        """Initialize the panel or adopt a matching, already initialized frame."""
        if self.opened:
            return
        try:
            self.transport.open()
            self._transport_open = True
            if adopt_existing:
                # Firmware/build tools used the same controller configuration.
                # Do not reset, reinitialize or clear the preserved RAM.
                self._cmd(0xC7, bytes([self.config.brightness]))
                self._cmd(0xAF)
                self.opened = True
                return
            self.sleep(.3)  # Controller supply settling before commands.
            self.transport.reset()
            for command, parameters in initialization_commands(self.config):
                self._cmd(command, parameters)
            self._show_native(bytes(FRAME_BYTES))
            self._cmd(0xAF)
            self.sleep(.1)
            self.opened = True
        except BaseException:
            try:
                self.close()
            except Exception:
                pass
            raise

    def show(self, frame):
        """Validate, rotate and transmit one complete RGB565 frame."""
        if not self.opened:
            raise RuntimeError('SSD1351 not opened')
        self._show_native(native_bytes(frame, self.config))

    def set_brightness(self, value):
        """C7 master current 0..15 = factors 1/16..16/16, not display OFF."""
        if type(value) is not int or not 0 <= value <= 15:
            raise ValueError('brightness must be an integer 0..15')
        if not self.opened:
            raise RuntimeError('SSD1351 not opened')
        self._cmd(0xC7, bytes([value]))

    def close(self, *, blank=True):
        """Release owned I/O, optionally preserving display RAM for handover."""
        try:
            if self._transport_open and blank:
                self._cmd(0xAE)
        finally:
            self.opened = False
            if self._transport_open:
                self._transport_open = False
                self.transport.close()
