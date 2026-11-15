import tempfile
import unittest
from pathlib import Path
from PIL import Image
from unittest.mock import patch
from src.integrations.outputs.oled_status import image_to_frame, load_splash, status_frame, request_status, main

class OLEDStatusTests(unittest.TestCase):
    def test_image_8k(self):
        im=Image.new('L',(128,128),255)
        self.assertEqual(len(image_to_frame(im).data),8192)
        self.assertEqual(image_to_frame(im).data[0],255)
    def test_transparency_black(self):
        im=Image.new('RGBA',(2,2),(255,255,255,0))
        self.assertEqual(image_to_frame(im).data[0],0)
    def test_centered_small(self):
        im=Image.new('L',(16,8),255)
        frame=image_to_frame(im)
        self.assertEqual(frame.data[0],0)
        self.assertGreater(sum(frame.data),0)
    def test_bad_png_fallback(self):
        self.assertIsNone(load_splash('/tmp/nonexistent-sonoscd-test.png'))
    def test_valid_png(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'splash.png'
            Image.new('RGB',(64,64),(255,255,255)).save(path)
            self.assertIsNotNone(load_splash(path))
    def test_status_modes(self):
        st={'player':{'mode':'PAUSED','track':2,'track_count':15},'sonos':{'transport':'PAUSED_PLAYBACK'},'stream':{'active':True}}
        self.assertNotEqual(status_frame(st).data,status_frame(error='offline').data)
        self.assertEqual(len(status_frame(st).data),8192)
    def test_default_status_safe(self):
        self.assertEqual(len(status_frame({}).data),8192)
    def test_no_write_commands(self):
        class FakeSocket:
            def __init__(self): self.sent=[]
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def settimeout(self,*a):pass
            def connect(self,*a):pass
            def sendall(self,b):self.sent.append(b)
            def recv(self,n):return b'{"ok":true,"state":{"player":{"mode":"STOPPED"}}}\n'
        fake=FakeSocket()
        with patch('src.integrations.outputs.oled_status.socket.socket',return_value=fake):
            st=request_status()
        self.assertEqual(st['player']['mode'],'STOPPED')
        self.assertEqual(fake.sent,[b'{"command":"status"}\n'])
    def test_dry_run(self):
        with patch('src.integrations.outputs.oled_status.request_status',return_value={'player':{'mode':'STOPPED'}}):
            main(['--seconds','0.01','--refresh-ms','200','--splash-seconds','0'])
    def test_requires_gpio_execute_pair(self):
        with self.assertRaises(SystemExit):main(['--execute'])

if __name__=='__main__':unittest.main()
