"""M0.14a: opt-in Waveshare 1.5inch SSD1327 SPI0/CE0 test driver.

Hardware SPI: BCM10 MOSI, BCM11 SCLK, BCM8 CE0; DC and RESET configurable.
No player or Sonos commands. Hardware imports deferred until --execute.
"""
from __future__ import annotations
from dataclasses import dataclass
from .display_pins import DisplayPins
import time

WIDTH = HEIGHT = 128
FRAME_BYTES = WIDTH * HEIGHT // 2

@dataclass(frozen=True)
class DisplayConfig:
    bus: int = 0
    device: int = 0
    dc_pin: int = 25
    rst_pin: int = 24
    spi_hz: int = 2_000_000
    contrast: int = 0x80

    def __post_init__(self):
        DisplayPins(spi_bus=self.bus, spi_device=self.device,
                    dc_pin=self.dc_pin, rst_pin=self.rst_pin)
        if type(self.spi_hz) is not int or not 100_000 <= self.spi_hz <= 8_000_000:
            raise ValueError('SPI clock must be 100000..8000000 Hz')
        if type(self.contrast) is not int or not 0 <= self.contrast <= 255:
            raise ValueError('contrast must be 0..255')

class Framebuffer:
    """SSD1327 packed grayscale: first x uses high nibble; second x low nibble."""
    def __init__(self):
        self.data = bytearray(FRAME_BYTES)

    def clear(self, shade=0):
        self._shade(shade)
        self.data[:] = bytes([(shade << 4) | shade]) * FRAME_BYTES

    @staticmethod
    def _shade(shade):
        if type(shade) is not int or not 0 <= shade <= 15:
            raise ValueError('shade must be integer 0..15')

    def pixel(self, x, y, shade):
        self._shade(shade)
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            return
        i = y * (WIDTH // 2) + x // 2
        if x & 1:
            self.data[i] = (self.data[i] & 0xf0) | shade
        else:
            self.data[i] = (shade << 4) | (self.data[i] & 0x0f)

    def fill_rect(self, x, y, w, h, shade):
        self._shade(shade)
        for yy in range(max(0,y), min(HEIGHT,y+h)):
            for xx in range(max(0,x),min(WIDTH,x+w)):
                self.pixel(xx, yy, shade)

    def border(self, shade=15):
        for x in range(WIDTH):
            self.pixel(x,0,shade); self.pixel(x,HEIGHT-1,shade)
        for y in range(HEIGHT):
            self.pixel(0,y,shade); self.pixel(WIDTH-1,y,shade)

    def test_image(self, kind):
        self.clear()
        if kind == 'gray':
            for x in range(16):
                self.fill_rect(x*8,0,8,HEIGHT,x)
        elif kind == 'grid':
            for y in range(0,HEIGHT,16):
                self.fill_rect(0,y,WIDTH,1,8)
            for x in range(0,WIDTH,16):
                self.fill_rect(x,0,1,HEIGHT,8)
            self.border(15)
            self.fill_rect(3,3,11,11,15)
            self.fill_rect(114,114,11,11,5)
        elif kind == 'checker':
            for y in range(HEIGHT):
                for x in range(WIDTH):
                    self.pixel(x,y,15 if ((x//8+y//8)%2) else 0)
        elif kind == 'text':
            try:
                from PIL import Image, ImageDraw, ImageFont
            except ImportError as e:
                raise RuntimeError('Texttest erfordert Pillow (python3-pil)') from e
            im=Image.new('L',(WIDTH,HEIGHT),0)
            draw=ImageDraw.Draw(im)
            font=ImageFont.load_default()
            for y, label, gray in [(9,'SONOS CD',255),(32,'SSD1327',220),(55,'128 x 128',175),(78,'SPI0 / CE0',145),(101,'M0.14a',255)]:
                draw.text((8,y),label,font=font,fill=gray)
            p=im.load()
            for y in range(HEIGHT):
                for x in range(WIDTH):
                    self.pixel(x,y,round(p[x,y]*15/255))
        elif kind == 'clear':
            pass
        else:
            raise ValueError('unknown test image: '+kind)
        return self

class SSD1327:
    def __init__(self, config=DisplayConfig(), *, spi=None, gpio=None, sleep=time.sleep,
                 gpio_warnings=False):
        self.config=config; self.spi=spi; self.gpio=gpio; self.sleep=sleep; self.opened=False
        self.gpio_warnings=gpio_warnings

    def _cmd(self,*bs):
        self.gpio.output(self.config.dc_pin,self.gpio.LOW)
        self.spi.writebytes(list(bs))

    def _data(self,payload):
        self.gpio.output(self.config.dc_pin,self.gpio.HIGH)
        # spidev xfer/write size may be limited by kernel buf; chunk conservatively
        for at in range(0,len(payload),4096):
            self.spi.writebytes(list(payload[at:at+4096]))

    def open(self, *, adopt_existing=False):
        if self.opened: return
        try:
            if self.gpio is None:
                import RPi.GPIO as GPIO
                self.gpio=GPIO
            if self.spi is None:
                import spidev
                self.spi=spidev.SpiDev()
                self.spi.open(self.config.bus,self.config.device)
            self.spi.max_speed_hz=self.config.spi_hz
            self.spi.mode=0
            setwarnings=getattr(self.gpio,'setwarnings',None)
            if setwarnings is not None: setwarnings(bool(self.gpio_warnings))
            self.gpio.setmode(self.gpio.BCM)
            self.gpio.setup(self.config.dc_pin,self.gpio.OUT,initial=self.gpio.LOW)
            self.gpio.setup(self.config.rst_pin,self.gpio.OUT,initial=self.gpio.HIGH)
            if adopt_existing:
                # Firmware initialized display RAM: do not RESET or clear it.
                # A previous process may have sent AE (DISPLAY OFF) in close().
                # AF resumes that controller without disturbing the splash RAM.
                # SPI write success cannot prove the panel is connected or lit.
                self._cmd(0xaf)
                self.opened=True
                return
            self.gpio.output(self.config.rst_pin,self.gpio.LOW)
            self.sleep(.05)
            self.gpio.output(self.config.rst_pin,self.gpio.HIGH)
            self.sleep(.15)
            # 128x128 SSD1327 initialization (4-bit grayscale, horizontal increment).
            for cmd, args in [
                (0xfd,(0x12,)),(0xae,()),(0x15,(0,63)),(0x75,(0,127)),
                (0x81,(self.config.contrast,)),(0xa0,(0x51,)),
                (0xa1,(0,)),(0xa2,(0,)),(0xa4,()),(0xa8,(127,)),
                (0xab,(1,)),(0xb1,(0x11,)),(0xb3,(0,)),
                (0xb9,()),(0xbc,(0x08,)),(0xbe,(0x07,)),
                (0xb6,(1,)),(0xd5,(0x62,)),(0xaf,())
            ]:
                self._cmd(cmd,*args)
            self.opened=True
        except BaseException:
            self.close()
            raise

    def show(self,frame):
        if not self.opened: raise RuntimeError('display not opened')
        if not isinstance(frame, Framebuffer) or len(frame.data)!=FRAME_BYTES: raise ValueError('invalid framebuffer size')
        self._cmd(0x15,0,63)
        self._cmd(0x75,0,127)
        self._cmd(0x5c)  # SSD1327 WRITE_RAM: required before display data
        self._data(frame.data)

    def set_contrast(self, value):
        """Adjust OLED current without redrawing pixels (SSD1327 0x81)."""
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError('contrast must be an integer 0..255')
        if not self.opened:
            raise RuntimeError('display not opened')
        self._cmd(0x81, value)

    def recover_once(self, frame, *, contrast=None):
        """Explicit full reinitialization after a reported SPI I/O error.

        The caller must enforce the recovery budget. No panel-health polling
        is possible over this write-only SPI link. Never invoked on normal
        startup or firmware-splash adoption.
        """
        if not isinstance(frame, Framebuffer) or len(frame.data) != FRAME_BYTES:
            raise ValueError('recovery needs a valid last framebuffer')
        self.close()
        self.open(adopt_existing=False)
        if contrast is not None:
            self.set_contrast(contrast)
        self.show(frame)

    def close(self, *, blank=True):
        """Release SPI/GPIO; optionally preserve the last OLED frame for handover.

        The default remains DISPLAY OFF. A preserving close must only be used
        when an explicitly coordinated successor is taking over the display.
        """
        try:
            if blank and self.spi is not None and self.gpio is not None:
                self._cmd(0xae) # display off
        except Exception:
            pass
        try:
            if self.spi is not None: self.spi.close()
        finally:
            if self.gpio is not None:
                try: self.gpio.cleanup((self.config.dc_pin,self.config.rst_pin))
                except Exception: pass
            self.opened=False
