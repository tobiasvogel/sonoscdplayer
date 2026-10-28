import io
import tempfile
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from src.integrations.outputs.oled_ambient import (
    contrast_for_lux, OLEDAmbientConfig, OLEDContrastControl)
from src.integrations.outputs.oled_status import main

class TestOLEDAmbient(unittest.TestCase):
    def test_curve_exact_points(self):
        for lux, value in [(0,20),(1,32),(5,64),(18,128),(50,176),(100,208),(200,224)]:
            with self.subTest(lux=lux): self.assertEqual(contrast_for_lux(lux), value)
    def test_curve_monotonic_and_clamped(self):
        values=[contrast_for_lux(x/4) for x in range(0,1200)]
        self.assertEqual(values,sorted(values))
        self.assertEqual(values[-1],224)
        for lux in [-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError): contrast_for_lux(lux)
    def test_smooth_and_stale_fallback(self):
        readings=[100.0, OSError('no socket'), OSError('timeout'), OSError('offline')]
        def read():
            result=readings.pop(0)
            if isinstance(result,Exception): raise result
            return result
        c=OLEDContrastControl(OLEDAmbientConfig(poll_ms=1000,stale_ms=3000,smoothing_ms=1000),reader=read)
        self.assertEqual(c.tick(0),128)
        value=c.tick(1000)
        self.assertGreater(value,128)
        self.assertLess(value,208)
        value2=c.tick(2000)
        self.assertGreater(value2,value)
        self.assertLess(c.tick(5000),value2)
    def test_independent_of_led_and_rejects_invalid_config(self):
        for cfg in [dict(poll_ms=50),dict(stale_ms=100),dict(fallback_contrast=256),dict(socket_path='relative')]:
            with self.assertRaises(ValueError): OLEDAmbientConfig(**cfg)
    def test_disabled_default_dry_run(self):
        with redirect_stdout(io.StringIO()) as buf:
            main(['--seconds','0.01','--splash-seconds','0'])
        self.assertNotIn('oled_contrast',buf.getvalue())
    def test_ambient_offline_dry_run(self):
        # Isolate BOTH sockets: a running real player service must not alter this test.
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()) as buf:
                main(['--ambient-auto',
                      '--ambient-socket', str(Path(tmp) / 'missing-ambient.sock'),
                      '--socket', str(Path(tmp) / 'missing-player.sock'),
                      '--seconds','0.02','--splash-seconds','0','--refresh-ms','200'])
        self.assertIn('service_offline',buf.getvalue())
