"""Driver for the Waveshare SKU 18179 SH1107 OLED, over SPI or I2C.

Native geometry: 64 columns x 128 rows, 16 pages of 64 bytes (vertical LSB).
The default logical canvas is 128x64, rotated clockwise onto the native panel.
No luma dependency and no player/Sonos access. See docs/SH1107.md for sources.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from sonoscdplayer.ui.displays.backend import GraphicFrame, validate_frame
from sonoscdplayer.ui.displays.capabilities import DisplayCapabilities
from sonoscdplayer.hardware.displays.transport import DisplayTransport

NATIVE_WIDTH, NATIVE_HEIGHT = 64, 128
FRAME_BYTES = 1024
PIXEL_FORMAT = 'mono1-vlsb'


@dataclass(frozen=True)
class DisplayConfig:
    """Rotation, display offset and contrast of the SH1107."""
    rotation: int = 90
    contrast: int = 128
    display_offset: int = 0x60

    def __post_init__(self):
        if type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270):
            raise ValueError('rotation must be 0, 90, 180 or 270')
        for name, maximum in (('contrast', 255), ('display_offset', 127)):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f'{name} must be an integer 0..{maximum}')

    @property
    def size(self):
        """Logical (width, height) after rotation."""
        return (128, 64) if self.rotation in (90, 270) else (64, 128)

    @property
    def capabilities(self):
        """DisplayCapabilities of this configuration."""
        width, height = self.size
        return DisplayCapabilities(kind='graphic', width=width, height=height,
                                   pixel_format=PIXEL_FORMAT)


def image_to_frame(image, config=DisplayConfig()):
    """Threshold at 128, without dithering; pack vertical bytes explicitly."""
    if image.size != config.size:
        raise ValueError('image does not match logical display size')
    pixels = image.convert('L').load()
    width, height = config.size
    data = bytearray(FRAME_BYTES)
    for y in range(height):
        for x in range(width):
            if pixels[x, y] >= 128:
                data[(y // 8) * width + x] |= 1 << (y % 8)
    return GraphicFrame(width, height, PIXEL_FORMAT, bytes(data))


def native_bytes(frame, config=DisplayConfig()):
    """Map logical pixels to native panel coordinates before any I/O."""
    validate_frame(config.capabilities, frame)
    if config.rotation == 0:
        return frame.data
    result = bytearray(FRAME_BYTES)
    for y in range(frame.height):
        for x in range(frame.width):
            if not frame.data[(y // 8) * frame.width + x] & (1 << (y % 8)):
                continue
            if config.rotation == 90:
                nx, ny = 63 - y, x
            elif config.rotation == 180:
                nx, ny = 63 - x, 127 - y
            else:
                nx, ny = y, 127 - x
            result[(ny // 8) * NATIVE_WIDTH + nx] |= 1 << (ny % 8)
    return bytes(result)


class SH1107:
    """Driver for the SH1107 OLED over a DisplayTransport (SPI or I2C)."""
    def __init__(self, transport: DisplayTransport, config=DisplayConfig(), *,
                 sleep=time.sleep):
        self.transport = transport
        self.config = config
        self.capabilities = config.capabilities
        self.sleep = sleep
        self.opened = False

    def _cmd(self, *values):
        self.transport.command(bytes(values))

    def _show_native(self, data):
        # Horizontal addressing (0x20): columns increment within each page.
        for page in range(16):
            self._cmd(0xB0 | page, 0x00, 0x10)
            self.transport.data(data[page * 64:(page + 1) * 64])

    def open(self):
        """Reset and initialize the controller, display on."""
        if self.opened:
            return
        try:
            self.transport.open()
            self.transport.reset()
            self._cmd(0xAE)  # Keep panel off until RAM has been cleared.
            for command in (
                (0x20,), (0xA0,), (0xC0,), (0xA8, 0x7F),
                (0xD3, self.config.display_offset), (0xDC, 0x00),
                (0xD5, 0x50), (0xD9, 0x22), (0xDB, 0x35),
                (0xAD, 0x81),  # Internal DC/DC enabled, SH1107 datasheet.
                (0x81, self.config.contrast), (0xA4,), (0xA6,),
            ):
                self._cmd(*command)
            self._show_native(bytes(FRAME_BYTES))
            self._cmd(0xAF)
            self.sleep(.1)
            self.opened = True
        except BaseException:
            self.opened = False
            try:
                self._cmd(0xAE)
            except Exception:
                pass
            try:
                self.transport.close()
            except Exception:
                pass
            raise

    def show(self, frame):
        """Show a 1-bit frame from ``image_to_frame``."""
        if not self.opened:
            raise RuntimeError('display not opened')
        self._show_native(native_bytes(frame, self.config))

    def set_contrast(self, value):
        """Set the contrast (0..255)."""
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError('contrast must be an integer 0..255')
        if not self.opened:
            raise RuntimeError('display not opened')
        self._cmd(0x81, value)

    def close(self):
        """Display off and release the transport."""
        try:
            if self.opened:
                self._cmd(0xAE)
        finally:
            self.opened = False
            self.transport.close()
