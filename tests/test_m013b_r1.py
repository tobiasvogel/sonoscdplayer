"""M0.13b-r1 diagnostic runner regressions."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch
from src.integrations.outputs import led_pwm

class TestDiagnostics(unittest.TestCase):
    def test_selftest_no_socket_and_two_distinct_outputs(self):
        out=io.StringIO()
        with patch.object(led_pwm, 'load_config', return_value=led_pwm.OutputConfig(tick_ms=10)), \
             patch.object(led_pwm, 'read_status', side_effect=AssertionError('socket accessed')), \
             contextlib.redirect_stdout(out):
            self.assertEqual(led_pwm.main(['--self-test','--seconds','0.05']), 0)
        lines=[json.loads(s) for s in out.getvalue().splitlines()]
        self.assertEqual(lines[0]['source'], 'self_test_stage_1')
        self.assertEqual(lines[0]['power_pattern'], 'steady')
        self.assertEqual(lines[0]['connection_pattern'], 'off')

    def test_connection_override_remains_visible_when_socket_missing(self):
        out=io.StringIO()
        with patch.object(led_pwm, 'load_config', return_value=led_pwm.OutputConfig(tick_ms=10)), \
             patch.object(led_pwm, 'read_status', side_effect=FileNotFoundError('missing socket')), \
             contextlib.redirect_stdout(out):
            self.assertEqual(led_pwm.main(['--connection','ownership_lost','--seconds','0.05']), 0)
        first=json.loads(out.getvalue().splitlines()[0])
        self.assertIn('status_unavailable',first['source'])
        self.assertEqual(first['power_pattern'],'blink')
        self.assertEqual(first['connection_pattern'],'triple_flash')

    def test_renderer_steady_distinct_from_double_flash(self):
        cfg=led_pwm.OutputConfig()
        r=led_pwm.LedRenderer(cfg)
        state=led_pwm.LedState(led_pwm.Led(led_pwm.Pattern.STEADY,'test'),
                               led_pwm.Led(led_pwm.Pattern.DOUBLE_FLASH,'test'))
        self.assertEqual(r.tick(state,0),(.7,.7))
        self.assertEqual(r.tick(state,200),(.7,0.0))
        self.assertEqual(r.tick(state,350),(.7,.7))
        self.assertEqual(r.tick(state,900),(.7,0.0))
