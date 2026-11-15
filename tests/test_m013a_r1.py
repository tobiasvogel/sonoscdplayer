import unittest
from src.integrations.outputs.led_model import Pattern, Connection, PowerFault, derive_led_state
from src.integrations.outputs.led_patterns import PatternConfig, intensity, ErrorHold

def status(mode='STOPPED'):
    return {'schema_version': 1, 'player': {'mode': mode}}

class LedPatternsR1(unittest.TestCase):
    def test_power_modes(self):
        self.assertEqual(derive_led_state(status(), power_fault=PowerFault.DRIVE).power.pattern, Pattern.DOUBLE_FLASH)
        self.assertEqual(derive_led_state(status(), power_fault=PowerFault.CRITICAL).power.pattern, Pattern.TRIPLE_FLASH)
        self.assertEqual(derive_led_state(status(), shutting_down=True).power.pattern, Pattern.SLOW_BLINK)
        self.assertEqual(derive_led_state(status('ERROR')).power.reason, 'player_error_unclassified')
    def test_critical_priority(self):
        self.assertEqual(derive_led_state(status('ERROR'), power_fault=PowerFault.CRITICAL, shutting_down=True).power.pattern, Pattern.TRIPLE_FLASH)
    def test_connection_faults(self):
        self.assertEqual(derive_led_state(status(), connection=Connection.UNSAFE_TOPOLOGY).connection.pattern, Pattern.DOUBLE_FLASH)
        self.assertEqual(derive_led_state(status(), connection=Connection.OWNERSHIP_LOST).connection.pattern, Pattern.TRIPLE_FLASH)
        self.assertEqual(derive_led_state(status(), connection=Connection.UNREACHABLE).connection.pattern, Pattern.SLOW_BLINK)
    def test_both_independent(self):
        state=derive_led_state(status('PLAYING'), connection=Connection.OWNERSHIP_LOST)
        self.assertEqual(state.power.pattern,Pattern.STEADY)
    def test_off_overrides_errors(self):
        state=derive_led_state(status('ERROR'), operating=False, power_fault=PowerFault.CRITICAL, connection=Connection.OWNERSHIP_LOST)
        self.assertEqual((state.power.pattern,state.connection.pattern),(Pattern.OFF,Pattern.OFF))
    def test_flash_samples(self):
        cfg=PatternConfig(brightness=.7)
        for t,expected in [(0,.7),(149,.7),(150,0),(299,0),(300,.7),(449,.7),(450,0),(600,0),(2000,.7)]:
            self.assertEqual(intensity(Pattern.DOUBLE_FLASH,t,cfg),expected)
        self.assertEqual(intensity(Pattern.TRIPLE_FLASH,600,cfg),.7)
        self.assertEqual(intensity(Pattern.TRIPLE_FLASH,750,cfg),0)
    def test_steady_and_breathe(self):
        self.assertEqual(intensity(Pattern.OFF,100),0)
        self.assertEqual(intensity(Pattern.STEADY,100),.7)
        self.assertAlmostEqual(intensity(Pattern.BREATHE,0),.07)
        self.assertAlmostEqual(intensity(Pattern.BREATHE,1000),.7)
    def test_blink_samples(self):
        self.assertEqual(intensity(Pattern.SLOW_BLINK,500),0)
        self.assertEqual(intensity(Pattern.SLOW_BLINK,1000),.7)
        self.assertEqual(intensity(Pattern.FAST_BLINK,150),0)
        self.assertEqual(intensity(Pattern.FAST_BLINK,300),.7)
    def test_invalid_config(self):
        for kwargs in ({'brightness':2},{'brightness':float('nan')},{'flash_period_ms':100}, {'error_hold_ms':-1}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError): PatternConfig(**kwargs)
    def test_invalid_elapsed(self):
        for bad in (-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError): intensity(Pattern.STEADY,bad)
    def test_hold_and_expiry(self):
        hold=ErrorHold(PatternConfig(error_hold_ms=3000))
        bad=derive_led_state(status(),connection=Connection.UNREACHABLE)
        good=derive_led_state(status(),connection=Connection.REACHABLE)
        hold.update(bad,0)
        self.assertEqual(hold.update(good,2999).connection.pattern,Pattern.SLOW_BLINK)
        self.assertEqual(hold.update(good,3000).connection.pattern,Pattern.STEADY)
    def test_priority_preempts(self):
        hold=ErrorHold()
        hold.update(derive_led_state(status(),connection=Connection.UNREACHABLE),0)
        high=derive_led_state(status(),connection=Connection.OWNERSHIP_LOST)
        self.assertEqual(hold.update(high,100).connection.pattern,Pattern.TRIPLE_FLASH)
        self.assertEqual(hold.update(derive_led_state(status(),connection=Connection.REACHABLE),3010).connection.pattern,Pattern.TRIPLE_FLASH)
    def test_shutdown_clears_hold(self):
        hold=ErrorHold()
        hold.update(derive_led_state(status('ERROR')),0)
        off=derive_led_state(status('ERROR'),operating=False)
        self.assertEqual(hold.update(off,1,operating=False).power.pattern,Pattern.OFF)
        self.assertEqual(hold.update(derive_led_state(status()),2).power.pattern,Pattern.STEADY)
    def test_invalid_explicit_fault(self):
        with self.assertRaises(ValueError): derive_led_state(status(),power_fault='drive')
    def test_time_backward(self):
        h=ErrorHold(); s=derive_led_state(status()); h.update(s,5)
        with self.assertRaises(ValueError): h.update(s,4)

if __name__ == '__main__': unittest.main()
