"""SSD1327 OLED driver: SPI frames, display pins, GPIO warnings, recovery and fast resume (fake SPI/GPIO)."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from unittest.mock import Mock
ROOT = Path(__file__).resolve().parents[3]
from sonoscdplayer.hardware.displays.ssd1327 import (
    Framebuffer,
    DisplayConfig,
    SSD1327,
    FRAME_BYTES,
)
from sonoscdplayer.hardware.displays.ssd1327_test import main
from sonoscdplayer.hardware.displays.ssd1327_contrast_test import main as contrast_main, parse_levels
from sonoscdplayer.hardware.displays.ambient_contrast import (
    contrast_for_lux,
    OLEDAmbientConfig,
    OLEDContrastControl,
)
from sonoscdplayer.ui.display_service import main as status_main, player_ready_timeout
from sonoscdplayer.hardware.displays.pins import DisplayPins, load_display_pins
from tools.generate_ssd1327_display_header import render_header
from tools.build_ssd1327_splash import build
from sonoscdplayer.system.gpio_settings import apply_gpio_warnings, gpio_warnings_enabled
from sonoscdplayer.hardware.leds.led_service import OutputConfig, PwmOutputs
from tools import early_power_led
from sonoscdplayer.hardware.displays.ssd1327_recovery import RecoveryBudget



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
        with self.assertRaises(ValueError): DisplayConfig(dc_pin=8)
        with self.assertRaises(ValueError): DisplayConfig(dc_pin=24)
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
            contrast_main(['--levels','24,48','--seconds','0.01'])
        self.assertIn('"contrast": 48',out.getvalue())
    def test_explicit_hardware_authorization(self):
        for arguments in (['--gpio'],['--execute'],['--levels','500']):
            with self.subTest(arguments=arguments), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                contrast_main(arguments)
    def test_init_has_initial_contrast(self):
        spi=FakeSPI();d=SSD1327(DisplayConfig(contrast=22),spi=spi,gpio=FakeGPIO(),sleep=lambda _:None)
        d.open()
        self.assertIn(bytes([0x81,22]),spi.messages)
        d.close()


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
            status_main(['--seconds','0.01','--splash-seconds','0'])
        self.assertNotIn('oled_contrast',buf.getvalue())
    def test_ambient_offline_dry_run(self):
        # Isolate BOTH sockets: a running real player service must not alter this test.
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()) as buf:
                status_main(['--ambient-auto',
                      '--ambient-socket', str(Path(tmp) / 'missing-ambient.sock'),
                      '--socket', str(Path(tmp) / 'missing-player.sock'),
                      '--seconds','0.02','--splash-seconds','0','--refresh-ms','200'])
        self.assertIn('status_unavailable',buf.getvalue())


class DisplayPinConfigTests(unittest.TestCase):
    def test_toml_splash_and_header_share_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'local.toml'
            config.write_text('[display]\ncontroller = "ssd1327"\ninterface = "spi"\n'
                              'spi_bus = 0\nspi_device = 0\n'
                              'dc_pin = 22\nrst_pin = 27\n', encoding='utf-8')
            pins = load_display_pins(config)
            splash = build(bytes(8192), pins=pins)
            header = render_header(pins)
            self.assertIn('[dc 22]', splash)
            self.assertIn('RST27', splash)
            self.assertIn('#define SONOSCDPLAYER_OLED_DC_PIN 22u', header)
            self.assertIn('#define SONOSCDPLAYER_OLED_RST_PIN 27u', header)

    def test_unsupported_display_controllers_and_interfaces_fail_explicitly(self):
        for controller, interface in (('ssd1306', 'spi'), ('ssd1327', 'i2c'),
                                      ('ssd1327', 'I2C'), ('unknown', 'i2c')):
            with self.subTest(controller=controller, interface=interface):
                with self.assertRaisesRegex(ValueError, 'Unsupported display'):
                    DisplayPins(controller=controller, interface=interface)

    def test_toml_rejects_unimplemented_i2c_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'display.toml'
            config.write_text('[display]\ncontroller = "ssd1327"\n'
                              'interface = "i2c"\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Unsupported display interface'):
                load_display_pins(config)

    def test_invalid_pins_rejected(self):
        for dc, rst in ((22, 22), (8, 27), (22, 11), (True, 27),
                        (28, 27), (22, -1)):
            with self.subTest(dc=dc, rst=rst):
                with self.assertRaises(ValueError):
                    DisplayPins(dc_pin=dc, rst_pin=rst)

    def test_build_requires_explicit_local_display_config(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ['sh', str(ROOT / 'tools/build_shutdown_renderer.sh')],
                env={'PATH': '/usr/bin:/bin', 'BUILD_DIR': directory,
                     # never fall back to an installed /etc/sonoscdplayer
                     'SONOSCDPLAYER_CONFIG_DIR': str(Path(directory) / 'no-config')},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('DISPLAY_CONFIG', result.stderr)

    def test_build_replaces_stale_header_from_local_config(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            config = build / 'local.toml'
            header = build / 'ssd1327_display_config.h'
            config.write_text('[display]\ndc_pin = 22\nrst_pin = 27\n',
                              encoding='utf-8')
            header.write_text('#define SONOSCDPLAYER_OLED_DC_PIN 25u\n',
                              encoding='ascii')
            import os
            result = subprocess.run(
                ['sh', str(ROOT / 'tools/build_shutdown_renderer.sh')],
                env={**os.environ, 'BUILD_DIR': directory,
                     'DISPLAY_CONFIG': str(config)},
                capture_output=True, text=True, timeout=40,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            generated = header.read_text(encoding='ascii')
            self.assertIn('SONOSCDPLAYER_OLED_DC_PIN 22u', generated)
            self.assertIn('SONOSCDPLAYER_OLED_RST_PIN 27u', generated)
            self.assertNotIn('SONOSCDPLAYER_OLED_DC_PIN 25u', generated)

    def test_missing_config_does_not_reuse_stale_header(self):
        with tempfile.TemporaryDirectory() as directory:
            header = Path(directory) / 'ssd1327_display_config.h'
            header.write_text('#define SONOSCDPLAYER_OLED_DC_PIN 25u\n',
                              encoding='ascii')
            result = subprocess.run(
                ['sh', str(ROOT / 'tools/build_shutdown_renderer.sh')],
                env={'PATH': '/usr/bin:/bin', 'BUILD_DIR': directory,
                     # never fall back to an installed /etc/sonoscdplayer
                     'SONOSCDPLAYER_CONFIG_DIR': str(Path(directory) / 'no-config')},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('DISPLAY_CONFIG', result.stderr)

    def test_generator_cli_writes_expected_pin_constants(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'local.toml'
            header = Path(directory) / 'ssd1327_display_config.h'
            config.write_text('[display]\ndc_pin = 22\nrst_pin = 27\n',
                              encoding='utf-8')
            result = subprocess.run(
                [sys.executable, str(ROOT / 'tools/generate_ssd1327_display_header.py'),
                 '--config', str(config), '--output', str(header)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            source = header.read_text(encoding='ascii')
            self.assertIn('SONOSCDPLAYER_OLED_DC_PIN 22u', source)
            self.assertIn('SONOSCDPLAYER_OLED_RST_PIN 27u', source)


class WarningsFakeGPIO:
    BCM = 'BCM'; OUT = 'OUT'; IN = 'IN'; LOW = 0; HIGH = 1

    def __init__(self):
        self.calls = []

    def setwarnings(self, value):
        self.calls.append(('setwarnings', value))

    def setmode(self, mode):
        self.calls.append(('setmode', mode))

    def setup(self, *args, **kwargs):
        self.calls.append(('setup', args[0]))

    def output(self, *args):
        pass

    def cleanup(self, *args):
        pass

    def PWM(self, pin, hz):
        return mock.Mock()


class GpioWarningsTests(unittest.TestCase):
    def write(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'player.toml'
        path.write_text(text)
        return path

    def test_default_is_off_and_example_matches(self):
        self.assertFalse(gpio_warnings_enabled(self.write('')))
        self.assertFalse(gpio_warnings_enabled(ROOT / 'config/player.example.toml'))
        self.assertFalse(gpio_warnings_enabled(Path('/nonexistent/player.toml')))
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {'SONOSCDPLAYER_CONFIG_DIR': directory}):
            self.assertFalse(gpio_warnings_enabled())
            (Path(directory) / 'player.toml').write_text('[gpio]\nwarnings = true\n')
            self.assertTrue(gpio_warnings_enabled())

    def test_invalid_values_are_rejected(self):
        for text in ('gpio = 1\n', '[gpio]\nwarnings = "yes"\n', '[gpio]\nwarnings = 1\n'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    gpio_warnings_enabled(self.write(text))

    def test_apply_tolerates_backends_without_setwarnings(self):
        apply_gpio_warnings(object(), True)
        gpio = WarningsFakeGPIO()
        apply_gpio_warnings(gpio, True)
        self.assertEqual(gpio.calls, [('setwarnings', True)])

    def test_led_outputs_set_warnings_before_claiming_pins(self):
        for enabled in (False, True):
            gpio = WarningsFakeGPIO()
            PwmOutputs(OutputConfig(), gpio, gpio_warnings=enabled).open()
            self.assertEqual(gpio.calls[0], ('setwarnings', enabled))

    def test_oled_sets_warnings_before_claiming_pins(self):
        gpio = WarningsFakeGPIO()
        driver = SSD1327(DisplayConfig(), spi=mock.Mock(), gpio=gpio, sleep=lambda _s: None,
                         gpio_warnings=True)
        driver.open(adopt_existing=True)
        self.assertEqual(gpio.calls[0], ('setwarnings', True))

    def test_early_led_reads_pin_and_warnings_from_one_file(self):
        path = self.write('[gpio]\nwarnings = true\n[led_outputs]\npower_pin = 13\nconnection_pin = 12\n')
        self.assertEqual(early_power_led.configured_settings(path), (13, True))
        self.assertEqual(early_power_led.configured_power_pin(path), 13)
        with self.assertRaises(ValueError):
            early_power_led.configured_settings(self.write('[gpio]\nwarnings = "no"\n'))


class RecoveryTests(unittest.TestCase):
    def test_one_attempt_only(self):
        driver=Mock()
        frame=Framebuffer()
        budget=RecoveryBudget(1)
        self.assertTrue(budget.run(driver,frame,contrast=83))
        self.assertFalse(budget.run(driver,frame,contrast=83))
        driver.recover_once.assert_called_once_with(frame,contrast=83)

    def test_failure_consumes_budget(self):
        driver=Mock()
        driver.recover_once.side_effect=OSError('spi failed')
        budget=RecoveryBudget(1)
        with self.assertRaises(OSError):
            budget.run(driver,Framebuffer())
        self.assertFalse(budget.run(driver,Framebuffer()))

    def test_recovery_disabled(self):
        driver=Mock()
        self.assertFalse(RecoveryBudget(0).run(driver,Framebuffer()))
        driver.recover_once.assert_not_called()

    def test_invalid_budget(self):
        for attempts in (-1,4,True):
            with self.subTest(attempts=attempts),self.assertRaises(ValueError):
                RecoveryBudget(attempts)

    def test_driver_requires_valid_frame_before_touching_hardware(self):
        driver=SSD1327(DisplayConfig(),spi=Mock(),gpio=Mock())
        with self.assertRaises(ValueError):
            driver.recover_once(None)
        driver.spi.close.assert_not_called()


class OLEDHandoverFastResumeTests(unittest.TestCase):
    def test_default_boot_wait_unchanged(self):
        self.assertEqual(player_ready_timeout(30.0, preserve=True, environ={}), 30.0)
        self.assertEqual(player_ready_timeout(30.0, preserve=False, environ={}), 30.0)

    def test_flag_without_preserve_is_inert(self):
        env = {'SONOSCDPLAYER_HANDOVER_FAST_RESUME': '1'}
        self.assertEqual(player_ready_timeout(30.0, preserve=False, environ=env), 30.0)

    def test_exact_opt_in_shortens_wait(self):
        env = {'SONOSCDPLAYER_HANDOVER_FAST_RESUME': '1'}
        self.assertEqual(player_ready_timeout(30.0, preserve=True, environ=env), 2.0)

    def test_never_increases_custom_short_timeout(self):
        env = {'SONOSCDPLAYER_HANDOVER_FAST_RESUME': '1'}
        self.assertEqual(player_ready_timeout(1.0, preserve=True, environ=env), 1.0)

    def test_other_env_values_do_not_enable_fast_resume(self):
        for value in ('true', 'yes', '0', ''):
            with self.subTest(value=value):
                self.assertEqual(
                    player_ready_timeout(
                        30.0, preserve=True,
                        environ={'SONOSCDPLAYER_HANDOVER_FAST_RESUME': value}),
                    30.0)


if __name__ == '__main__':
    unittest.main()
