import io
import unittest
from contextlib import redirect_stdout
from src.integrations.outputs.ssd1327 import SSD1327, Framebuffer, DisplayConfig
from src.integrations.outputs.oled_contrast import main, parse_levels
from tests.test_m014a import FakeGPIO, FakeSPI

class TestManualContrast(unittest.TestCase):
    def test_valid_range(self):
        self.assertEqual(parse_levels('0,16,255'),(0,16,255))
    def test_invalid_sequences(self):
        for seq in ('','-1','256','1,','x',','.join(['1']*33)):
            with self.subTest(seq=seq), self.assertRaises(ValueError): parse_levels(seq)
    def test_contrast_command_without_frame_transfer(self):
        spi=FakeSPI(); gpio=FakeGPIO(); d=SSD1327(spi=spi,gpio=gpio,sleep=lambda _:None)
        d.open(); before=len(spi.messages)
        d.set_contrast(12); d.set_contrast(255)
        self.assertEqual(spi.messages[before:],[bytes((0x81,12)),bytes((0x81,255))])
        d.close()
    def test_not_open_and_invalid_values(self):
        d=SSD1327(spi=FakeSPI(),gpio=FakeGPIO(),sleep=lambda _:None)
        with self.assertRaises(RuntimeError): d.set_contrast(128)
        d.open()
        for val in (-1,256,1.5,True,'12'):
            with self.subTest(val=val), self.assertRaises(ValueError): d.set_contrast(val)
        d.close()
    def test_dry_run_does_not_access_gpio(self):
        with redirect_stdout(io.StringIO()) as out:
            main(['--levels','24,48','--seconds','0.01'])
        self.assertIn('"contrast": 48',out.getvalue())
    def test_explicit_hardware_authorization(self):
        for arguments in (['--gpio'],['--execute'],['--levels','500']):
            with self.subTest(arguments=arguments), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                main(arguments)
    def test_init_has_initial_contrast(self):
        spi=FakeSPI();d=SSD1327(DisplayConfig(contrast=22),spi=spi,gpio=FakeGPIO(),sleep=lambda _:None)
        d.open()
        self.assertIn(bytes([0x81,22]),spi.messages)
        d.close()
