"""Ambient integration tests: pure data path, no sensor/GPIO required."""
import math
import tempfile
import unittest
from pathlib import Path
from src.integrations.outputs.ambient_brightness import AmbientConfig, parse_lux, lux_to_brightness, BrightnessSmoother
from src.integrations.outputs.led_pwm import LedRenderer, OutputConfig, load_config
from src.integrations.outputs.led_model import LedState,Led,Pattern

class TestAmbient(unittest.TestCase):
    def test_valid_plain_number(self): self.assertEqual(parse_lux(b'21.5\n'),21.5)
    def test_valid_decimal_formats(self):
        for raw, value in ((b'0',0.), (b'2.75\n',2.75), (b'  .5\r\n',.5), (b'120.0',120.), (b'10.',10.)):
            with self.subTest(raw=raw): self.assertEqual(parse_lux(raw),value)
    def test_invalid_responses(self):
        for raw in (b'',b'NaN',b'-2',b'{"lux":2.75}',b'{"lux":true}',b'{"value":12}',b'[1]',b'x',b'{"lux": "12"}',b'Inf',b'1e3',b'2 lux',b'1 2',b'+2',b'x'*2049):
            with self.subTest(raw=raw[:20]),self.assertRaises(ValueError): parse_lux(raw)
    def test_lux_clamp_and_monotone(self):
        c=AmbientConfig()
        self.assertAlmostEqual(lux_to_brightness(0,c),c.min_brightness)
        self.assertAlmostEqual(lux_to_brightness(1e6,c),c.max_brightness)
        self.assertLess(lux_to_brightness(10,c),lux_to_brightness(100,c))
    def test_bad_config(self):
        for opts in ({'lux_dark':10,'lux_bright':2},{'min_brightness':0.9,'max_brightness':0.3},{'timeout_ms':1},{'stale_ms':100,'poll_ms':500},{'socket_path':'relative'}):
            with self.subTest(opts=opts),self.assertRaises(ValueError): AmbientConfig(**opts)
    def test_smoothing_and_fallback(self):
        c=AmbientConfig(smoothing_ms=1000)
        s=BrightnessSmoother(c)
        self.assertEqual(s.tick(0.1,0),c.fallback_brightness)
        moved=s.tick(0.1,1000)
        self.assertTrue(0.1<moved<c.fallback_brightness)
        self.assertGreater(s.tick(c.fallback_brightness,2000),moved)
    def test_immediate_mode(self): self.assertEqual(BrightnessSmoother(AmbientConfig(smoothing_ms=0)).tick(.2,0),.2)
    def test_reject_nonmonotonic(self):
        s=BrightnessSmoother(AmbientConfig());s.tick(.2,100)
        with self.assertRaises(ValueError):s.tick(.1,90)
    def test_ambient_config_nested(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.toml';p.write_text('[led_outputs]\nbrightness=0.7\n[led_outputs.ambient]\nenabled=true\npoll_ms=2000\n')
            c=load_config(str(p))
            self.assertTrue(c.ambient.enabled)
            self.assertEqual(c.ambient.poll_ms,2000)
    def test_ambient_config_unknown_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.toml';p.write_text('[led_outputs.ambient]\nunknown=1\n')
            with self.assertRaises(ValueError):load_config(str(p))
    def test_led_output_scales_both_without_changing_pattern(self):
        r=LedRenderer(OutputConfig(brightness=.7))
        state=LedState(Led(Pattern.STEADY,'test'),Led(Pattern.DOUBLE_FLASH,'test'))
        self.assertAlmostEqual(r.tick(state,0,brightness=.2)[0],.2)
        second=r.tick(state,200,brightness=.2)
        self.assertAlmostEqual(second[0],.2)
        self.assertEqual(second[1],0.0)
    def test_disabled_baseline_unchanged(self):
        r=LedRenderer(OutputConfig())
        state=LedState(Led(Pattern.STEADY,'test'),Led(Pattern.OFF,'test'))
        self.assertEqual(r.tick(state,0),(.7,0.0))

if __name__=='__main__': unittest.main()
