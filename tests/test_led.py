"""Status LEDs: state model, patterns, PWM output, pin mapping and diagnostic runner (no GPIO)."""
import contextlib
import io
import json
import os
import socket
import tempfile
import threading
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from integrations.outputs.led_model import (
    Network,
    Pattern,
    classify_network,
    derive_led_state,
    PowerFault,
)
from integrations.outputs.led_patterns import PatternConfig, intensity, ErrorHold
from integrations.outputs.led_pwm import (
    OutputConfig,
    PwmOutputs,
    LedRenderer,
    load_config,
    read_status,
)
from integrations.outputs import led_pwm
from tools.early_power_led import configured_power_pin



def snapshot(mode='STOPPED', **sonos):
    return {'schema_version': 1, 'player': {'mode': mode, 'track': 1, 'track_count': 15, 'error': None},
            'sonos': {'owned': False, 'transport': 'STOPPED', 'target_uid': None, **sonos},
            'stream': {'active': False}}


class LedModelTests(unittest.TestCase):
    def test_operational_modes_steady_even_paused_and_no_disc(self):
        for mode in ('NO_DISC', 'STOPPED', 'PLAYING', 'PAUSED'):
            with self.subTest(mode=mode):
                self.assertEqual(derive_led_state(snapshot(mode)).power.pattern, Pattern.STEADY)

    def test_starting_breathes(self):
        self.assertEqual(derive_led_state(snapshot('STARTING')).power.pattern, Pattern.BREATHE)

    def test_error_blinks(self):
        self.assertEqual(derive_led_state(snapshot('ERROR')).power.pattern, Pattern.FAST_BLINK)

    def test_off_is_explicit_not_no_disc(self):
        result = derive_led_state(snapshot('NO_DISC'), operating=False, network=Network.CONNECTED)
        self.assertEqual(result.power.pattern, Pattern.OFF)
        self.assertEqual(result.connection.pattern, Pattern.OFF)

    def test_connection_led_ignores_sonos_state(self):
        # Sonos ownership or transport never lights the network LED.
        result = derive_led_state(snapshot('PLAYING', owned=True, transport='PLAYING', target_uid='FAKE_TEST_UID'))
        self.assertEqual(result.connection.pattern, Pattern.OFF)
        self.assertEqual(result.connection.reason, 'network_unknown')

    def test_network_states(self):
        expected = {Network.UNKNOWN: Pattern.OFF, Network.CONNECTED: Pattern.STEADY,
                    Network.CONNECTING: Pattern.BREATHE, Network.DISCONNECTED: Pattern.SLOW_BLINK,
                    Network.HOTSPOT: Pattern.FAST_BLINK}
        self.assertEqual(set(expected), set(Network))
        for observation, pattern in expected.items():
            with self.subTest(observation=observation):
                self.assertEqual(derive_led_state(snapshot(), network=observation).connection.pattern, pattern)

    def test_network_independent_of_player_mode(self):
        for mode in ('NO_DISC', 'STOPPED', 'STARTING', 'PLAYING', 'PAUSED', 'ERROR'):
            with self.subTest(mode=mode):
                self.assertEqual(derive_led_state(snapshot(mode), network=Network.CONNECTED).connection.pattern, Pattern.STEADY)

    def test_classify_network_status(self):
        from sonos_ui.network_status import NetworkStatus, parse_device_status
        cases = (
            (NetworkStatus(), Network.UNKNOWN),                                   # nmcli failed
            (parse_device_status('eth0:ethernet:connected\nwlan0:wifi:disconnected'), Network.CONNECTED),
            (parse_device_status('eth0:ethernet:unavailable\nwlan0:wifi:connected'), Network.CONNECTED),
            (parse_device_status('eth0:ethernet:unavailable\nwlan0:wifi:connecting'), Network.CONNECTING),
            (parse_device_status('eth0:ethernet:disconnected\nwlan0:wifi:disconnected'), Network.DISCONNECTED),
            (parse_device_status('lo:loopback:unmanaged'), Network.UNKNOWN),       # no LAN/WLAN device
        )
        for status, expected in cases:
            with self.subTest(status=status):
                self.assertEqual(classify_network(status), expected)

    def test_unknown_mode_fails_closed(self):
        with self.assertRaises(ValueError):
            derive_led_state(snapshot('UNRECOGNIZED'))

    def test_bad_schema_fails_closed(self):
        bad = snapshot()
        bad['schema_version'] = 2
        with self.assertRaises(ValueError):
            derive_led_state(bad)

    def test_invalid_network_observation_rejected(self):
        with self.assertRaises(ValueError):
            derive_led_state(snapshot(), network='connected')

    def test_input_is_not_mutated(self):
        original = snapshot('PLAYING')
        saved = {key: dict(value) if isinstance(value, dict) else value for key, value in original.items()}
        derive_led_state(original)
        self.assertEqual(original, saved)

    def test_model_has_no_gpio_dependencies(self):
        import subprocess, sys
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / 'src'
        code = ('import sys, integrations.outputs.led_model; '
                'print(any(m.split(".")[0] in ("RPi", "gpiozero") for m in sys.modules))')
        result = subprocess.run([sys.executable, '-c', code], cwd=src,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.stdout.strip(), 'False', result.stderr)


def status(mode='STOPPED'):
    return {'schema_version': 1, 'player': {'mode': mode}}


class LedPatternTests(unittest.TestCase):
    def test_power_modes(self):
        self.assertEqual(derive_led_state(status(), power_fault=PowerFault.DRIVE).power.pattern, Pattern.DOUBLE_FLASH)
        self.assertEqual(derive_led_state(status(), shutting_down=True).power.pattern, Pattern.SLOW_BLINK)
        self.assertEqual(derive_led_state(status('ERROR')).power.reason, 'player_error_unclassified')
    def test_shutting_down_wins(self):
        self.assertEqual(derive_led_state(status('ERROR'), power_fault=PowerFault.DRIVE, shutting_down=True).power.pattern, Pattern.SLOW_BLINK)
    def test_missing_drive_from_status(self):
        missing={'schema_version':1,'player':{'mode':'NO_DISC'},'disc':{'mode':'NO_DRIVE','device':None,'track_count':0,'error':None}}
        self.assertEqual(derive_led_state(missing).power.pattern, Pattern.DOUBLE_FLASH)
        missing['disc']['mode']='NO_DISC'
        self.assertEqual(derive_led_state(missing).power.pattern, Pattern.STEADY)
        missing['disc']['mode']='NO_DRIVE'; missing['player']['mode']='ERROR'
        self.assertEqual(derive_led_state(missing).power.pattern, Pattern.DOUBLE_FLASH)
    def test_both_independent(self):
        state=derive_led_state(status('PLAYING'), network=Network.DISCONNECTED)
        self.assertEqual(state.power.pattern,Pattern.STEADY)
        self.assertEqual(state.connection.pattern,Pattern.SLOW_BLINK)
    def test_off_overrides_errors(self):
        state=derive_led_state(status('ERROR'), operating=False, power_fault=PowerFault.DRIVE, network=Network.DISCONNECTED)
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
        bad=derive_led_state(status('ERROR'))
        good=derive_led_state(status())
        hold.update(bad,0)
        self.assertEqual(hold.update(good,2999).power.pattern,Pattern.FAST_BLINK)
        self.assertEqual(hold.update(good,3000).power.pattern,Pattern.STEADY)
    def test_priority_preempts(self):
        hold=ErrorHold()
        hold.update(derive_led_state(status('ERROR')),0)
        high=derive_led_state(status(),power_fault=PowerFault.DRIVE)
        self.assertEqual(hold.update(high,100).power.pattern,Pattern.DOUBLE_FLASH)
        self.assertEqual(hold.update(derive_led_state(status()),3010).power.pattern,Pattern.DOUBLE_FLASH)
    def test_shutdown_is_not_held_back_by_an_error(self):
        hold=ErrorHold()
        hold.update(derive_led_state(status('ERROR')),0)
        down=derive_led_state(status('ERROR'),shutting_down=True)
        self.assertEqual(hold.update(down,10).power.pattern,Pattern.SLOW_BLINK)

    def test_network_led_is_not_held(self):
        hold=ErrorHold()
        hold.update(derive_led_state(status(),network=Network.DISCONNECTED),0)
        self.assertEqual(hold.update(derive_led_state(status(),network=Network.CONNECTED),10).connection.pattern,
                         Pattern.STEADY)
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


class LedPwmConfigTests(unittest.TestCase):
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
        state=derive_led_state({'schema_version':1,'player':{'mode':'STARTING'}},network=Network.CONNECTING)
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
        self.assertEqual(render.tick(ok,1350)[0],0)
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

    def test_network_led_stays_visible_when_player_socket_missing(self):
        out=io.StringIO()
        with patch.object(led_pwm, 'load_config', return_value=led_pwm.OutputConfig(tick_ms=10)), \
             patch.object(led_pwm, 'read_status', side_effect=FileNotFoundError('missing socket')), \
             contextlib.redirect_stdout(out):
            self.assertEqual(led_pwm.main(['--network','disconnected','--seconds','0.05']), 0)
        first=json.loads(out.getvalue().splitlines()[0])
        # Never answered yet: still starting, breathing instead of an error.
        self.assertIn('waiting_for_player',first['source'])
        self.assertEqual(first['power_pattern'],'breathe')
        self.assertEqual(first['connection_pattern'],'slow_blink')

    def test_unreachable_player_is_an_error_after_the_start_time(self):
        out=io.StringIO()
        with patch.object(led_pwm, 'load_config', return_value=led_pwm.OutputConfig(tick_ms=10)), \
             patch.object(led_pwm, 'read_status', side_effect=FileNotFoundError('missing socket')), \
             patch.object(led_pwm, 'STARTUP_GRACE_SECONDS', 0.0), \
             contextlib.redirect_stdout(out):
            self.assertEqual(led_pwm.main(['--network','disconnected','--seconds','0.05']), 0)
        first=json.loads(out.getvalue().splitlines()[0])
        self.assertIn('status_unavailable',first['source'])
        self.assertEqual(first['power_pattern'],'fast_blink')

    def test_unavailable_status_after_first_answer_is_an_error(self):
        status_, why = led_pwm.unavailable_status(0.0, 5.0, last_good_at=1.0)
        self.assertEqual((status_['player']['mode'], why), ('ERROR', 'status_unavailable'))
        status_, why = led_pwm.unavailable_status(0.0, 5.0, last_good_at=None)
        self.assertEqual((status_['player']['mode'], why), ('STARTING', 'waiting_for_player'))

    def test_shutdown_detection(self):
        done = lambda out: subprocess.CompletedProcess([], 0, out, '')
        with patch.object(led_pwm.subprocess, 'run', return_value=done('stopping\n')):
            self.assertTrue(led_pwm.read_shutting_down())
        with patch.object(led_pwm.subprocess, 'run', return_value=done('running\n')):
            self.assertFalse(led_pwm.read_shutting_down())
        with patch.object(led_pwm.subprocess, 'run', side_effect=OSError('no systemctl')):
            self.assertFalse(led_pwm.read_shutting_down())

    def test_service_reads_the_network_state_in_the_background(self):
        out=io.StringIO()
        calls=[]
        def network():
            calls.append(1)
            return led_pwm.Network.CONNECTED
        with patch.object(led_pwm, 'load_config', return_value=led_pwm.OutputConfig(tick_ms=10)), \
             patch.object(led_pwm, 'read_status', return_value={'schema_version':1,'player':{'mode':'STOPPED'}}), \
             patch.object(led_pwm, 'read_network', side_effect=network), \
             contextlib.redirect_stdout(out):
            self.assertEqual(led_pwm.main(['--seconds','0.3']), 0)
        patterns=[json.loads(line)['connection_pattern'] for line in out.getvalue().splitlines()]
        self.assertEqual(patterns[0],'off')            # unknown until the first reading
        self.assertEqual(patterns[-1],'steady')
        self.assertEqual(len(calls),1)                 # every NETWORK_POLL_SECONDS, not every tick

    def test_failing_network_reader_shows_unknown(self):
        out=io.StringIO()
        with patch.object(led_pwm, 'load_config', return_value=led_pwm.OutputConfig(tick_ms=10)), \
             patch.object(led_pwm, 'read_status', return_value={'schema_version':1,'player':{'mode':'STOPPED'}}), \
             patch.object(led_pwm, 'read_network', side_effect=OSError('no nmcli')), \
             contextlib.redirect_stdout(out):
            self.assertEqual(led_pwm.main(['--seconds','0.2']), 0)
        patterns={json.loads(line)['connection_pattern'] for line in out.getvalue().splitlines()}
        self.assertEqual(patterns,{'off'})

    def test_renderer_steady_distinct_from_double_flash(self):
        cfg=led_pwm.OutputConfig()
        r=led_pwm.LedRenderer(cfg)
        state=led_pwm.LedState(led_pwm.Led(led_pwm.Pattern.STEADY,'test'),
                               led_pwm.Led(led_pwm.Pattern.DOUBLE_FLASH,'test'))
        self.assertEqual(r.tick(state,0),(.7,.7))
        self.assertEqual(r.tick(state,200),(.7,0.0))
        self.assertEqual(r.tick(state,350),(.7,.7))
        self.assertEqual(r.tick(state,900),(.7,0.0))


class LedPinMappingTests(unittest.TestCase):
    def test_default_and_swapped_mapping(self):
        default = OutputConfig()
        swapped = OutputConfig(power_pin=13, connection_pin=12)
        self.assertEqual((default.power_pin, default.connection_pin), (12, 13))
        self.assertEqual((swapped.power_pin, swapped.connection_pin), (13, 12))

    def test_invalid_and_duplicate_pins_rejected(self):
        for power, connection in ((12, 12), (13, 13), (19, 20),
                                  (12, 19), (True, 13), (12, False)):
            with self.subTest(power=power, connection=connection):
                with self.assertRaises(ValueError):
                    OutputConfig(power_pin=power, connection_pin=connection)

    def test_same_toml_mapping_for_regular_and_early_boot(self):
        for power, connection in ((12, 13), (13, 12)):
            with self.subTest(power=power):
                with TemporaryDirectory() as directory:
                    path = Path(directory) / 'player.toml'
                    path.write_text(
                        f'[led_outputs]\npower_pin = {power}\nconnection_pin = {connection}\n',
                        encoding='utf-8',
                    )
                    self.assertEqual(load_config(str(path)).power_pin, power)
                    self.assertEqual(configured_power_pin(path), power)

    def test_early_boot_rejects_invalid_mapping(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'player.toml'
            path.write_text('[led_outputs]\npower_pin = 12\nconnection_pin = 12\n',
                            encoding='utf-8')
            with self.assertRaises(ValueError):
                configured_power_pin(path)


if __name__ == '__main__':
    unittest.main()
