import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src'


class StackDumpTests(unittest.TestCase):
    def test_sigusr1_writes_thread_stacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ('import sys,time,tempfile;sys.path.insert(0,%r);import stack_dump;'
                    'print(stack_dump.enable("t"),flush=True);time.sleep(30)' % str(SRC))
            env = dict(os.environ, TMPDIR=tmp)
            proc = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE,
                                    text=True, env=env)
            try:
                path = proc.stdout.readline().strip()
                proc.send_signal(signal.SIGUSR1)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and 'hread' not in Path(path).read_text():
                    time.sleep(0.05)
                self.assertIn('Current thread', Path(path).read_text())
                self.assertIsNone(proc.poll())        # process keeps running
            finally:
                proc.kill(); proc.wait()


if __name__ == '__main__':
    unittest.main()
