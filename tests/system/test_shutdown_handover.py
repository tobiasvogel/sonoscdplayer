"""OLED handover at shutdown: SSD1327 exit, SIGTERM, systemd ordering and the shutdown sentinel (fake hardware)."""
import os
import select
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
from sonoscdplayer.hardware.displays.ssd1327 import SSD1327, DisplayConfig, Framebuffer
from sonoscdplayer.ui.display_service import (
    preserve_display_on_exit,
    preserve_display_for_shutdown,
)
FAKE_SPI = textwrap.dedent("""
    import os
    def log(event):
        with open(os.environ['SONOSCDPLAYER_FAKE_HW_LOG'], 'a') as stream:
            stream.write(event + '\\n')
    class SpiDev:
        def open(self, bus, device):
            log('spi_open')
        def writebytes(self, values):
            if values == [0xae]:
                log('display_off')
            elif values == [0xaf]:
                log('display_on')
        def close(self):
            log('spi_close')
""")
FAKE_GPIO = textwrap.dedent("""
    import os
    BCM = 11
    OUT = 1
    LOW = 0
    HIGH = 1
    def log(event):
        with open(os.environ['SONOSCDPLAYER_FAKE_HW_LOG'], 'a') as stream:
            stream.write(event + '\\n')
    def setmode(_mode):
        pass
    def setup(_pin, _mode, initial=None):
        pass
    def output(_pin, _value):
        pass
    def cleanup(_pins):
        log('gpio_cleanup')
""")
UNIT = Path(__file__).resolve().parents[2] / "systemd/sonoscdplayer-oled-shutdown-sentinel.service"



class FakeSPI:
    def __init__(self):
        self.commands = []
        self.closed = False
        self.max_speed_hz = None
        self.mode = None

    def writebytes(self, values):
        self.commands.append(tuple(values))

    def close(self):
        self.closed = True


class FakeGPIO:
    BCM = 11
    OUT = 1
    LOW = 0
    HIGH = 1

    def __init__(self):
        self.cleaned = []
        self.outputs = []

    def setmode(self, mode):
        pass

    def setup(self, pin, mode, initial=None):
        pass

    def output(self, pin, level):
        self.outputs.append((pin, level))

    def cleanup(self, pins):
        self.cleaned.append(pins)


class ExitHandoverTests(unittest.TestCase):
    def new_opened_driver(self):
        spi, gpio = FakeSPI(), FakeGPIO()
        driver = SSD1327(DisplayConfig(), spi=spi, gpio=gpio, sleep=lambda _: None)
        driver.open(adopt_existing=True)
        self.assertIn((0xaf,), spi.commands)
        spi.commands.clear()
        gpio.outputs.clear()
        return driver, spi, gpio

    def test_default_close_turns_display_off(self):
        driver, spi, gpio = self.new_opened_driver()
        driver.close()
        self.assertEqual(spi.commands, [(0xae,)])
        self.assertTrue(spi.closed)
        self.assertEqual(gpio.cleaned, [(25, 24)])
        self.assertFalse(driver.opened)

    def test_preserving_close_keeps_display_on_and_releases_resources(self):
        driver, spi, gpio = self.new_opened_driver()
        driver.close(blank=False)
        self.assertEqual(spi.commands, [])
        self.assertEqual(gpio.outputs, [])
        self.assertTrue(spi.closed)
        self.assertEqual(gpio.cleaned, [(25, 24)])
        self.assertFalse(driver.opened)

    def test_service_restart_adopts_preserved_frame_without_reset(self):
        first, first_spi, first_gpio = self.new_opened_driver()
        frame = Framebuffer()
        frame.pixel(0, 0, 15)
        first.show(frame)
        self.assertIn((0x5c,), first_spi.commands)
        first.close(blank=False)
        self.assertNotIn((0xae,), first_spi.commands)
        self.assertTrue(first_spi.closed)
        self.assertEqual(first_gpio.cleaned, [(25, 24)])

        # A restarted service takes over with a new SPI/GPIO file descriptor,
        # without a hardware reset or an OLED DISPLAY OFF command.
        second_spi, second_gpio = FakeSPI(), FakeGPIO()
        second = SSD1327(DisplayConfig(), spi=second_spi,
                         gpio=second_gpio, sleep=lambda _: None)
        second.open(adopt_existing=True)
        self.assertEqual(second_spi.commands, [(0xaf,)])
        self.assertNotIn((24, second_gpio.LOW), second_gpio.outputs)
        second.show(frame)
        self.assertIn((0x5c,), second_spi.commands)
        second.close()
        self.assertIn((0xae,), second_spi.commands)
        self.assertTrue(second_spi.closed)

    def test_normal_close_remains_default_after_preserved_handover(self):
        first, spi, _gpio = self.new_opened_driver()
        first.close(blank=False)
        second, second_spi, _second_gpio = self.new_opened_driver()
        second.close()
        self.assertEqual(spi.commands, [])
        self.assertEqual(second_spi.commands, [(0xae,)])

    def test_recovery_still_blanks_and_reinitializes(self):
        driver, spi, gpio = self.new_opened_driver()
        driver.recover_once(Framebuffer())
        self.assertIn((0xae,), spi.commands)
        self.assertTrue(driver.opened)


class ShutdownHandoverFakeHardwareTests(unittest.TestCase):
    def renderer_sigterm(self, preserve):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'RPi').mkdir()
            (directory / 'RPi/__init__.py').write_text('')
            (directory / 'RPi/GPIO.py').write_text(FAKE_GPIO)
            (directory / 'spidev.py').write_text(FAKE_SPI)
            log = directory / 'hardware.log'
            env = dict(os.environ)
            env['PYTHONPATH'] = os.pathsep.join((str(directory), str(ROOT / 'src'),
                                                  env.get('PYTHONPATH', '')))
            env['SONOSCDPLAYER_FAKE_HW_LOG'] = str(log)
            # Hardware mode reads [display] pins from the standard player.toml.
            (directory / 'player.toml').write_bytes(
                (ROOT / 'config/player.example.toml').read_bytes())
            env['SONOSCDPLAYER_CONFIG_DIR'] = str(directory)
            env['SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT'] = '1' if preserve else '0'
            proc = subprocess.Popen([
                sys.executable, '-u', '-m', 'sonoscdplayer.ui.display_service',
                '--gpio', '--execute', '--adopt-boot-splash',
                '--splash-seconds', '0', '--splash-ready-timeout', '0.1',
                '--seconds', '0', '--refresh-ms', '200',
                '--socket', str(directory / 'missing-control.sock')],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                ready, _, _ = select.select([proc.stdout], [], [], 8.0)
                self.assertTrue(ready, 'OLED renderer did not start')
                self.assertTrue(proc.stdout.readline(), 'renderer did not produce a status')
                self.assertIsNone(proc.poll(), 'renderer terminated before SIGTERM')
                proc.send_signal(signal.SIGTERM)
                _, errors = proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 0, errors)
                events = log.read_text().splitlines()
                self.assertIn('display_on', events)
                self.assertIn('spi_close', events)
                self.assertIn('gpio_cleanup', events)
                self.assertLess(events.index('display_on'), events.index('spi_close'))
                self.assertEqual('display_off' in events, not preserve)
                return events
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

    def test_systemd_sigterm_preserving_handover_releases_resources(self):
        self.renderer_sigterm(preserve=True)

    def test_systemd_sigterm_default_close_still_blanks(self):
        self.renderer_sigterm(preserve=False)


class ShutdownSigtermTests(unittest.TestCase):
    def test_renderer_exits_cleanly_on_sigterm(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + env.get('PYTHONPATH', '')
        process = subprocess.Popen(
            [sys.executable, '-m', 'sonoscdplayer.ui.display_service',
             '--splash-seconds', '0', '--seconds', '0', '--refresh-ms', '200',
             '--socket', '/tmp/sonoscdplayer-nonexistent-shutdown-test.sock'],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([process.stdout], [], [], 8.0)
            self.assertTrue(ready, 'renderer did not become ready for SIGTERM')
            self.assertTrue(process.stdout.readline(), 'renderer exited before status output')
            self.assertIsNone(process.poll(), 'renderer terminated unexpectedly')
            process.send_signal(signal.SIGTERM)
            _, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, errors)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


class ShutdownHandoverSystemdTests(unittest.TestCase):
    def test_preserve_is_disabled_without_opt_in(self):
        self.assertFalse(preserve_display_on_exit(False, {}))
        self.assertFalse(preserve_display_on_exit(False, {'SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT': '0'}))
        self.assertFalse(preserve_display_on_exit(False, {'SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT': 'true'}))

    def test_explicit_flag_or_systemd_environment_enables_preserve(self):
        self.assertTrue(preserve_display_on_exit(True, {}))
        self.assertTrue(preserve_display_on_exit(False, {'SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT': '1'}))

    def test_early_shutdown_unit_waits_for_oled_stop(self):
        unit = (ROOT / 'systemd/sonoscdplayer-shutdown-screen.service').read_text()
        self.assertIn('Before=sonoscdplayer-oled.service', unit)
        self.assertIn('RemainAfterExit=yes', unit)
        self.assertIn('ConditionFileIsExecutable=', unit)
        self.assertIn('ExecStop=-/usr/local/libexec/sonoscdplayer-shutdown-display transition', unit)
        self.assertNotIn('safetounplug', unit.lower())
        self.assertNotIn('WantedBy=multi-user.target', unit)

    def test_late_hook_selects_only_poweroff_and_reboot(self):
        script = (ROOT / 'systemd/sonoscdplayer-final-shutdown-hook').read_text()
        self.assertIn('poweroff) phase=final', script)
        self.assertIn('reboot) phase=reboot', script)
        self.assertIn('*) exit 0', script)
        self.assertNotIn('phase=safe', script)
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            hook = Path(directory) / 'hook.sh'
            bin_dir = Path(directory) / 'bin'
            bin_dir.mkdir()
            log = Path(directory) / 'phases'
            binary = bin_dir / 'renderer'
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "' + str(log) + '"\n')
            binary.chmod(0o755)
            hook.write_text(script.replace('/usr/local/libexec/sonoscdplayer-shutdown-display', str(binary)))
            for action in ('poweroff', 'reboot', 'halt', 'kexec', 'unknown'):
                self.assertEqual(subprocess.run(['sh', str(hook), action], check=False).returncode, 0)
            self.assertEqual(log.read_text().splitlines(), ['final', 'reboot'])

    def test_systemd_unit_parser_accepts_shutdown_service(self):
        import shutil
        import subprocess
        analyzer = shutil.which('systemd-analyze')
        if analyzer is None:
            self.skipTest('systemd-analyze is not installed')
        unit = ROOT / 'systemd/sonoscdplayer-shutdown-screen.service'
        result = subprocess.run([analyzer, 'verify', str(unit)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shutdown_hook_missing_renderer_fails_open(self):
        import subprocess
        import tempfile
        script = (ROOT / 'systemd/sonoscdplayer-final-shutdown-hook').read_text()
        with tempfile.TemporaryDirectory() as directory:
            absent = Path(directory) / 'does-not-exist'
            hook = Path(directory) / 'hook.sh'
            hook.write_text(script.replace(
                '/usr/local/libexec/sonoscdplayer-shutdown-display', str(absent)))
            for action in ('poweroff', 'reboot', 'halt', 'kexec'):
                result = subprocess.run(['sh', str(hook), action],
                                        capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_shutdown_hook_renderer_error_does_not_block_shutdown(self):
        import subprocess
        import tempfile
        script = (ROOT / 'systemd/sonoscdplayer-final-shutdown-hook').read_text()
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'failing-renderer'
            log = Path(directory) / 'calls'
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "' +
                              str(log) + '"\nexit 7\n')
            binary.chmod(0o755)
            hook = Path(directory) / 'hook.sh'
            hook.write_text(script.replace(
                '/usr/local/libexec/sonoscdplayer-shutdown-display', str(binary)))
            for action in ('poweroff', 'reboot'):
                result = subprocess.run(['sh', str(hook), action],
                                        capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text().splitlines(), ['final', 'reboot'])

    def test_shutdown_hooks_do_not_claim_safe_to_unplug(self):
        from pathlib import Path
        unit = (ROOT / 'systemd/sonoscdplayer-shutdown-screen.service').read_text()
        script = (ROOT / 'systemd/sonoscdplayer-final-shutdown-hook').read_text()
        for text in (unit, script):
            self.assertNotIn('safetounplug_screen.png', text)
            self.assertNotIn(' phase=safe', text)
        self.assertIn('phase=final', script)
        self.assertIn('phase=reboot', script)

    def test_dropin_does_not_override_installed_renderer_command(self):
        conf = (ROOT / 'systemd/sonoscdplayer-oled.service.d/exit-handover.conf.example').read_text()
        self.assertIn('Environment=SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT=1', conf)
        self.assertNotIn('ExecStart=', conf)
        self.assertNotIn('--ui-target-rpc', conf)


class ShutdownSentinelTests(unittest.TestCase):
    def test_default_close_is_not_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "flag"
            self.assertFalse(preserve_display_for_shutdown(environ={}, marker_path=marker))

    def test_marker_enables_preservation(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "flag"
            marker.touch()
            self.assertTrue(preserve_display_for_shutdown(environ={}, marker_path=marker))
            self.assertFalse(preserve_display_on_exit(environ={}))

    def test_manual_opt_in_still_supported(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "flag"
            self.assertTrue(preserve_display_for_shutdown(environ={"SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT":"1"}, marker_path=marker))

    def test_service_order(self):
        service = UNIT.read_text()
        self.assertIn("After=sonoscdplayer-oled.service", service)
        self.assertIn("ExecStop=/usr/bin/touch /run/sonoscdplayer-oled-shutdown-preserve", service)
        self.assertIn("RemainAfterExit=yes", service)
        self.assertIn("WantedBy=sonoscdplayer.target", service)


if __name__ == '__main__':
    unittest.main()
