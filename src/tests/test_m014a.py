import unittest
from src.integrations.outputs.ssd1327 import Framebuffer, DisplayConfig, SSD1327, FRAME_BYTES
from src.integrations.outputs.oled_test import main

class FakeGPIO:
    BCM=11; OUT=1; LOW=0; HIGH=1
    def __init__(self): self.actions=[]
    def setmode(self,*a): self.actions.append(('mode',a))
    def setup(self,*a,**k): self.actions.append(('setup',a,k))
    def output(self,*a): self.actions.append(('output',a))
    def cleanup(self,*a): self.actions.append(('cleanup',a))
class FakeSPI:
    def __init__(self): self.messages=[]; self.closed=False
    def writebytes(self,a): self.messages.append(bytes(a))
    def close(self): self.closed=True

class Tests(unittest.TestCase):
    def test_frame_size(self): self.assertEqual(FRAME_BYTES,8192)
    def test_nibble_pair(self):
        fb=Framebuffer(); fb.pixel(0,0,15); fb.pixel(1,0,3)
        self.assertEqual(fb.data[0],0xf3)
        fb.pixel(1,0,0); self.assertEqual(fb.data[0],0xf0)
    def test_bounds(self):
        fb=Framebuffer(); fb.pixel(-1,0,15);fb.pixel(128,0,15)
        self.assertEqual(sum(fb.data),0)
    def test_bad_shade(self):
        with self.assertRaises(ValueError): Framebuffer().clear(16)
    def test_patterns(self):
        for pattern in ['gray','grid','checker','text','clear']:
            with self.subTest(pattern=pattern):
                fb=Framebuffer().test_image(pattern)
                self.assertEqual(len(fb.data),FRAME_BYTES)
    def test_gradient(self):
        fb=Framebuffer().test_image('gray')
        self.assertEqual(fb.data[0],0)
        self.assertEqual(fb.data[63],255)
    def test_config_guards(self):
        with self.assertRaises(ValueError): DisplayConfig(dc_pin=23)
        with self.assertRaises(ValueError): DisplayConfig(spi_hz=100)
    def test_dry_run_no_gpio(self): main(['--pattern','gray'])
    def test_explicit_execute(self):
        with self.assertRaises(SystemExit): main(['--execute'])
    def test_spi_driver_mock(self):
        gpio=FakeGPIO();spi=FakeSPI(); d=SSD1327(gpio=gpio,spi=spi,sleep=lambda _:None)
        d.open();d.show(Framebuffer().test_image('grid'))
        self.assertEqual(sum(len(m) for m in spi.messages if len(m)==4096),8192)
        self.assertIn(bytes([0xaf]),spi.messages)
        # No framebuffer transfer without SSD1327 WRITE_RAM (0x5c).
        frame_at=next(i for i,m in enumerate(spi.messages) if len(m)==4096)
        self.assertEqual(spi.messages[frame_at-1], bytes([0x5c]))
        d.close();self.assertTrue(spi.closed)
        self.assertIn(bytes([0xae]),spi.messages)
    def test_off_on_exception(self):
        gpio=FakeGPIO();spi=FakeSPI(); d=SSD1327(gpio=gpio,spi=spi,sleep=lambda _:None)
        d.open()
        with self.assertRaises(ValueError): d.show(object())
        d.close();self.assertTrue(spi.closed)

class WriteRamRegression(unittest.TestCase):
    def test_write_ram_precedes_each_frame(self):
        gpio=FakeGPIO();spi=FakeSPI(); d=SSD1327(gpio=gpio,spi=spi,sleep=lambda _:None)
        d.open()
        for shade in (0,15):
            before=len(spi.messages)
            fb=Framebuffer();fb.clear(shade)
            d.show(fb)
            written=spi.messages[before:]
            self.assertEqual(written[0],bytes([0x15,0,63]))
            self.assertEqual(written[1],bytes([0x75,0,127]))
            self.assertEqual(written[2],bytes([0x5c]))
            self.assertEqual(sum(map(len,written[3:])),8192)
        d.close()
