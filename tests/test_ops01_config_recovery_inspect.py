"""Verify interrupted OPS-01 transactions in an independent Python process.

Only inherited FD points into an automatically-created temporary directory.
No CLI accepts a path to a live configuration. No child process writes files.
"""
import json
import os
import subprocess
import sys
import unittest

from tools.config_transaction_sandbox import OfflineSandbox, TransactionRejected

CURRENT = (b'[audio_startup]\nprebuffer_before_sonos = false\n'
           b'\n[service]\nexperimental_safe_screen = true\n')
TEMPLATE = (b'[audio_startup]\nprebuffer_before_sonos = true\n'
            b'pcm_timeout_seconds = 45\n'
            b'\n[service]\nexperimental_safe_screen = false\n')
KEYS = ('audio_startup.pcm_timeout_seconds',)

CHILD = """
import json, sys
from tools.config_recovery_inspect import inspect_persisted_transaction
from tools.config_recovery_inspect import RecoveryInspectionError
try:
    # Only passed-in, already-open temporary directory descriptor is used.
    result = inspect_persisted_transaction(int(sys.argv[1]), bytes.fromhex(sys.argv[2]))
except RecoveryInspectionError:
    print(json.dumps({'status': 'rejected'}))
    sys.exit(2)
print(json.dumps({'status': result}))
"""


@unittest.skipUnless(os.name == 'posix', 'requires POSIX pass_fds')
class IndependentRecoveryInspectionTests(unittest.TestCase):
    def _inspect_in_new_python(self, sandbox, template=TEMPLATE):
        process = subprocess.run(
            [sys.executable, '-c', CHILD,
             str(sandbox._dirfd), template.hex()],
            pass_fds=(sandbox._dirfd,),
            stdin=subprocess.DEVNULL,
            text=True, capture_output=True, timeout=10,
            check=False,
        )
        self.assertFalse(process.stderr, process.stderr)
        return process.returncode, json.loads(process.stdout)

    def test_new_process_recognizes_committed_candidate(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            sandbox.simulate_process_restart()
            code, report = self._inspect_in_new_python(sandbox)
            self.assertEqual(code, 0)
            self.assertEqual(report['status'], 'replacement_verified')
            self.assertIn(b'pcm_timeout_seconds = 45', sandbox.read_current())

    def test_new_process_recognizes_unreplaced_original(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old,
                              fail_at='before_replace')
            sandbox.simulate_process_restart()
            code, report = self._inspect_in_new_python(sandbox)
            self.assertEqual(code, 0)
            self.assertEqual(report['status'], 'not_replaced')
            self.assertEqual(sandbox.read_current(), CURRENT)

    def test_new_process_refuses_modified_current(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            changed = b'[external]\nkeep = "THIS_VALUE"\n'
            (sandbox.directory / 'current.toml').write_bytes(changed)
            sandbox.simulate_process_restart()
            code, report = self._inspect_in_new_python(sandbox)
            self.assertEqual(code, 2)
            self.assertEqual(report['status'], 'rejected')
            self.assertEqual(sandbox.read_current(), changed)

    def test_new_process_refuses_changed_reference(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            sandbox.simulate_process_restart()
            code, report = self._inspect_in_new_python(
                sandbox, TEMPLATE + b'\n# changed\n')
            self.assertEqual(code, 2)
            self.assertEqual(report['status'], 'rejected')

    def test_new_process_refuses_backup_symlink(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            path = sandbox.directory / 'backup-original.toml'
            path.unlink()
            path.symlink_to(sandbox.directory / 'current.toml')
            sandbox.simulate_process_restart()
            code, report = self._inspect_in_new_python(sandbox)
            self.assertEqual(code, 2)
            self.assertEqual(report['status'], 'rejected')


if __name__ == '__main__':
    unittest.main()
