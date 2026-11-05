"""Check the renderer's systemd SIGTERM exit without OLED hardware."""
from pathlib import Path
import os
import select
import signal
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ShutdownSigtermTests(unittest.TestCase):
    def test_renderer_exits_cleanly_on_sigterm(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + env.get('PYTHONPATH', '')
        process = subprocess.Popen(
            [sys.executable, '-m', 'integrations.outputs.oled_status',
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


if __name__ == '__main__':
    unittest.main()
