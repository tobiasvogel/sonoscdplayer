"""The APDS9306 daemon never serves an old lux value as current."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'src/integrations/sensors/apds-9306-065/service'


@unittest.skipIf(shutil.which('gcc') is None, 'gcc not installed')
class DaemonTests(unittest.TestCase):
    def test_reply_logic(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'logic'
            subprocess.run(['gcc', '-Wall', '-Wextra', '-Werror', '-I', str(SERVICE),
                            str(ROOT / 'tests/c/test_apds9306_logic.c'), '-o', str(binary)],
                           check=True)
            subprocess.run([str(binary)], check=True)

    def test_daemon_compiles_without_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(SERVICE / 'apds9306d.c'), '-o', str(Path(tmp) / 'd')],
                           check=True)


if __name__ == '__main__':
    unittest.main()
