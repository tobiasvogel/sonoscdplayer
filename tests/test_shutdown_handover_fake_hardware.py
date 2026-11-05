"""Exercise actual OLED process shutdown with fake SPI/GPIO modules.

No Raspberry Pi hardware or systemd installation required.
"""
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
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
            env['SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT'] = '1' if preserve else '0'
            proc = subprocess.Popen([
                sys.executable, '-u', '-m', 'integrations.outputs.oled_status',
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


if __name__ == '__main__':
    unittest.main()
