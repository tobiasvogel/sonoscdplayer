"""Experimental Waveshare SKU 13892, ST7735S 128x160 RGB565 controller.

Bus and backlight are injected. No hardware imports or player access here.
Rotation is in software; native MADCTL and panel offsets stay fixed.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from sonos_ui.display_backend import GraphicFrame, validate_frame
from sonos_ui.display_capabilities import DisplayCapabilities
from .display_transport import DisplayTransport
from .display_backlight import Backlight

NATIVE_WIDTH, NATIVE_HEIGHT = 128, 160
FRAME_BYTES = 40960


@dataclass(frozen=True)
class DisplayConfig:
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
        return (160, 128) if self.rotation in (90, 270) else (128, 160)

    @property
    def capabilities(self):
        width, height = self.size
        return DisplayCapabilities(kind='graphic', width=width, height=height,
                                   pixel_format='rgb565')


def image_to_frame(image, config=DisplayConfig()):
    """Canonical RGB565: row-major, red bits 15..11, high byte first."""
    if image.size != config.size:
        raise ValueError('image does not match logical display size')
    data = bytearray(FRAME_BYTES)
    pixels = image.convert('RGB').tobytes()
    for offset in range(0, len(pixels), 3):
        red, green, blue = pixels[offset:offset+3]
        index = offset // 3
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        data[2*index:2*index+2] = value.to_bytes(2, 'big')
    return GraphicFrame(*config.size, 'rgb565', bytes(data))


def native_bytes(frame, config=DisplayConfig()):
    validate_frame(config.capabilities, frame)
    if config.rotation == 0:
        return frame.data
    result = bytearray(FRAME_BYTES)
    for y in range(frame.height):
        for x in range(frame.width):
            if config.rotation == 90:
                nx, ny = 127-y, x
            elif config.rotation == 180:
                nx, ny = 127-x, 159-y
            else:
                nx, ny = y, 159-x
            source, target = 2*(y*frame.width+x), 2*(ny*128+nx)
            result[target:target+2] = frame.data[source:source+2]
    return bytes(result)


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
        if not self.opened:
            raise RuntimeError('display not opened')
        data = native_bytes(frame, self.config)  # Validate before bus or BL I/O.
        self._show_native(data)
        self.backlight.set(True)

    def close(self):
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
