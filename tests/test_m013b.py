import json
import os
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from src.integrations.outputs.led_pwm import OutputConfig, PwmOutputs, LedRenderer, load_config, read_status
from src.integrations.outputs.led_model import derive_led_state, Connection

class FakePWM:
    def __init__(self, pin, hz): self.pin=pin; self.hz=hz; self.duties=[]; self.stopped=False
    def start(self, val): self.duties.append(val)
    def ChangeDutyCycle(self, val): self.duties.append(val)
    def stop(self): self.stopped=True

class FakeGPIO:
    BCM=11; OUT=1; LOW=0; HIGH=1
    def __init__(self): self.pwms=[]; self.setups=[]; self.cleaned=[]; self.outputs=[]
    def setmode(self, mode): self.mode=mode
    def setup(self, pin, direction, initial): self.setups.append((pin,direction,initial))
    def PWM(self, pin,hz):
        p=FakePWM(pin,hz); self.pwms.append(p); return p
    def output(self,pin,val): self.outputs.append((pin,val))
    def cleanup(self,pins): self.cleaned.append(pins)

class TestM013b(unittest.TestCase):
    def test_config_defaults(self): self.assertEqual((OutputConfig().power_pin, OutputConfig().connection_pin),(12,13))
    def test_pin_restriction(self):
        with self.assertRaises(ValueError): OutputConfig(power_pin=14)
    def test_frequency_validation(self):
        with self.assertRaises(ValueError): OutputConfig(pwm_hz=0)
    def test_brightness_validation(self):
        with self.assertRaises(ValueError): OutputConfig(brightness=float('nan'))
    def test_config_load(self):
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('[led_outputs]\nbrightness=0.25\n'); name=f.name
        try: self.assertEqual(load_config(name).brightness,.25)
        finally: os.unlink(name)
    def test_config_rejects_unknown(self):
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('[led_outputs]\nmagic=1\n'); name=f.name
        try:
            with self.assertRaises(ValueError): load_config(name)
        finally: os.unlink(name)
    def test_open_write_close(self):
        g=FakeGPIO(); p=PwmOutputs(OutputConfig(),g)
        with p:
            p.write(.3,.5)
            self.assertEqual(g.pwms[0].duties[-1],30)
            self.assertEqual(g.pwms[1].duties[-1],50)
        self.assertTrue(all(x.stopped for x in g.pwms))
        self.assertEqual(g.cleaned,[ (12,13) ])
        self.assertEqual(g.outputs,[(12,0),(13,0)])
    def test_active_low(self):
        g=FakeGPIO()
        with PwmOutputs(OutputConfig(active_high=False), g) as p:
            p.write(.2,.8)
            self.assertAlmostEqual(g.pwms[0].duties[-1],80)
            self.assertAlmostEqual(g.pwms[1].duties[-1],20)
        self.assertEqual(g.outputs,[(12,1),(13,1)])
    def test_write_requires_open(self):
        with self.assertRaises(RuntimeError): PwmOutputs(OutputConfig(),FakeGPIO()).write(0,0)
    def test_invalid_duty(self):
        with PwmOutputs(OutputConfig(),FakeGPIO()) as p:
            with self.assertRaises(ValueError): p.write(-1,0)
    def test_breathe_over_time(self):
        render=LedRenderer(OutputConfig())
        state=derive_led_state({'schema_version':1,'player':{'mode':'STARTING'}},connection=Connection.CONNECTING)
        a=render.tick(state,1000); b=render.tick(state,1500)
        self.assertGreater(b[0],a[0])
        self.assertGreater(b[1],a[1])
    def test_steady_and_off(self):
        render=LedRenderer(OutputConfig())
        state=derive_led_state({'schema_version':1,'player':{'mode':'STOPPED'}})
        self.assertEqual(render.tick(state,0),(.7,0))
    def test_error_hold(self):
        render=LedRenderer(OutputConfig(error_hold_ms=3000))
        err=derive_led_state({'schema_version':1,'player':{'mode':'ERROR'}})
        ok=derive_led_state({'schema_version':1,'player':{'mode':'STOPPED'}})
        render.tick(err,0)
        self.assertEqual(render.tick(ok,1000)[0],.7)
        self.assertEqual(render.tick(ok,1500)[0],0)
        self.assertEqual(render.tick(ok,3001)[0],.7)
    def test_status_uses_readonly_command(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,'control.sock')
            server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); server.bind(path); server.listen(1)
            got=[]
            def serve():
                c,_=server.accept()
                with c:
                    got.append(c.recv(1024))
                    c.sendall(b'{"ok":true,"state":{"schema_version":1,"player":{"mode":"STOPPED"}}}\n')
            t=threading.Thread(target=serve); t.start()
            try:
                state=read_status(path)
                self.assertEqual(state['schema_version'],1)
                self.assertEqual(json.loads(got[0])['command'],'status')
            finally: t.join(2); server.close()

if __name__ == '__main__': unittest.main()
