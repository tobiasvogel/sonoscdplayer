"""Driver for the Waveshare SKU 13892 ST7735S color LCD (128x160, RGB565).

Bus and backlight are injected. No hardware imports or player access here.
Rotation is in software; native MADCTL and panel offsets stay fixed.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from sonoscdplayer.ui.displays.capabilities import DisplayCapabilities
from sonoscdplayer.hardware.displays.transport import DisplayTransport
from sonoscdplayer.hardware.displays.backlight import Backlight
from sonoscdplayer.hardware.displays import rgb565

NATIVE_WIDTH, NATIVE_HEIGHT = 128, 160
FRAME_BYTES = 40960


@dataclass(frozen=True)
class DisplayConfig:
    """Rotation, color order and panel offsets of the ST7735S."""
    rotation: int = 0
    x_offset: int = 2
    y_offset: int = 1
    color_order: str = 'rgb'

    def __post_init__(self):
        if type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270):
            raise ValueError('rotation must be 0, 90, 180 or 270')
        for name, maximum in (('x_offset', 4), ('y_offset', 2)):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f'{name} must be an integer 0..{maximum}')
        if self.color_order not in ('rgb', 'bgr'):
            raise ValueError('color order must be rgb or bgr')

    @property
    def size(self):
        """Logical (width, height) after rotation."""
        return (160, 128) if self.rotation in (90, 270) else (128, 160)

    @property
    def capabilities(self):
        """DisplayCapabilities of this configuration."""
        width, height = self.size
        return DisplayCapabilities(kind='graphic', width=width, height=height,
                                   pixel_format='rgb565')


def image_to_frame(image, config=DisplayConfig()):
    """Canonical RGB565: row-major, red bits 15..11, high byte first."""
    return rgb565.image_to_frame(image, config.size)


def native_bytes(frame, config=DisplayConfig()):
    """RGB565 bytes in the panel's native orientation for a frame."""
    return rgb565.native_bytes(frame, native_size=(NATIVE_WIDTH, NATIVE_HEIGHT),
                               rotation=config.rotation)


# Frame-rate and power register values follow the common ST7735 R/S setup;
# panel-specific geometry/color must be confirmed on the physical SKU 13892.
# See docs/ST7735S.md for controller and initialization references.
POWER_SETUP = (
    (0xB1, (0x01, 0x2C, 0x2D)), (0xB2, (0x01, 0x2C, 0x2D)),
    (0xB3, (0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D)), (0xB4, (0x07,)),
    (0xC0, (0xA2, 0x02, 0x84)), (0xC1, (0xC5,)),
    (0xC2, (0x0A, 0x00)), (0xC3, (0x8A, 0x2A)),
    (0xC4, (0x8A, 0xEE)), (0xC5, (0x0E,)),
)


class ST7735S:
    """Driver for the ST7735S color LCD over SPI, with GPIO backlight."""
    def __init__(self, transport: DisplayTransport, backlight: Backlight,
                 config=DisplayConfig(), *, sleep=time.sleep):
        self.transport = transport
        self.backlight = backlight
        self.config = config
        self.capabilities = config.capabilities
        self.sleep = sleep
        self.opened = False

    def _cmd(self, command, parameters=()):
        # ST7735S parameters use DC=1, unlike SH1107 command arguments.
        self.transport.command(bytes([command]))
        if parameters:
            self.transport.data(bytes(parameters))

    def _show_native(self, data):
        x, y = self.config.x_offset, self.config.y_offset
        self._cmd(0x2A, x.to_bytes(2, 'big') + (x+127).to_bytes(2, 'big'))
        self._cmd(0x2B, y.to_bytes(2, 'big') + (y+159).to_bytes(2, 'big'))
        self._cmd(0x2C)
        self.transport.data(data)

    def open(self):
        """Reset and initialize the controller, backlight on."""
        if self.opened:
            return
        try:
            self.transport.open()
            # Check GPIO9 after SPI has claimed its pinmux; then drive BL LOW
            # before reset or controller commands. This catches missing no_miso.
            self.backlight.open()
            self.transport.reset()
            self.sleep(.12)
            self._cmd(0x01)
            self.sleep(.15)
            self._cmd(0x11)
            self.sleep(.12)
            for command, parameters in POWER_SETUP:
                self._cmd(command, parameters)
            self._cmd(0x20)  # Normal polarity, not inverted.
            self._cmd(0x3A, (0x05,))  # 16-bit RGB565 input.
            self._cmd(0x36, (0xC0 | (0x08 if self.config.color_order == 'bgr' else 0),))
            self._cmd(0x13)
            self.sleep(.01)
            self._show_native(bytes(FRAME_BYTES))
            self._cmd(0x29)
            self.sleep(.1)
            self.opened = True
            # Backlight remains off until show() has uploaded a valid image.
        except BaseException:
            try:
                self.close()
            except Exception:
                pass
            raise

    def show(self, frame):
        """Show an RGB frame from ``image_to_frame``."""
        if not self.opened:
            raise RuntimeError('display not opened')
        data = native_bytes(frame, self.config)  # Validate before bus or BL I/O.
        self._show_native(data)
        self.backlight.set(True)

    def close(self):
        """Backlight off and release transport and backlight."""
        try:
            self.backlight.close()  # Turn off first, including after SPI failure.
        finally:
            try:
                if self.opened:
                    self._cmd(0x28)
                    self._cmd(0x10)
                    self.sleep(.12)
            finally:
                self.opened = False
                self.transport.close()
