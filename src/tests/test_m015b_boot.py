"""M0.15b isolated tests: no GPIO or Sonos access."""
import importlib.util
import unittest
from pathlib import Path
from PIL import Image
from src.integrations.outputs.ssd1327 import SSD1327, DisplayConfig
from src import frontends

ROOT=Path(__file__).resolve().parents[2]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class BootSplashTests(unittest.TestCase):
    def test_frame_and_ram_command(self):
        builder=load('splash_builder',Path('tools/build_ssd1327_splash.py'))
        image=Image.new('L',(128,128),0)
        image.putpixel((0,0),255)
        frame=builder.to_frame(image)
        self.assertEqual(len(frame),8192)
        self.assertEqual(frame[0],0xf0)
        source=builder.build(frame)
        self.assertEqual(source.count('oled 0x5c\n'),1)
        self.assertEqual(source.count('oled [DATA_ONLY]'),64)
        self.assertIn('oled 0xa0 0x51',source)
    def test_led_duty(self):
        led=load('early_led',Path('tools/early_power_led.py'))
        self.assertEqual(led.PIN,12)
        self.assertAlmostEqual(led.duty_at(0),7)
        self.assertAlmostEqual(led.duty_at(1),70)
        with self.assertRaises(ValueError): led.duty_at(-1)
    def test_frontend_adoption_opt_in(self):
        args=frontends.parser().parse_args([])
        self.assertFalse(args.adopt_boot_splash)
        cmds=dict(frontends.commands(args))
        self.assertNotIn('--adopt-boot-splash',cmds['oled'])
        args=frontends.parser().parse_args(['--adopt-boot-splash'])
        self.assertIn('--adopt-boot-splash',dict(frontends.commands(args))['oled'])
    def test_adopt_does_not_reset_or_send_commands(self):
        class GPIO:
            BCM=11; OUT=1; HIGH=1; LOW=0
            def __init__(self): self.events=[]
            def setmode(self,*a): self.events.append(('mode',a))
            def setup(self,*a,**kw): self.events.append(('setup',a,kw))
            def output(self,*a): self.events.append(('output',a))
            def cleanup(self,*a): pass
        class SPI:
            def __init__(self): self.messages=[]
            def writebytes(self,data): self.messages.append(data)
            def close(self): pass
        gpio=GPIO(); spi=SPI()
        driver=SSD1327(DisplayConfig(),gpio=gpio,spi=spi)
        driver.open(adopt_existing=True)
        self.assertTrue(driver.opened)
        self.assertFalse(spi.messages)
        self.assertFalse(any(x[0]=='output' for x in gpio.events))
        # Avoid close() turning display off in this test.
