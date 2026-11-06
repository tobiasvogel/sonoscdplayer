"""OPS-01 cooperating-writer lock checks in disposable sandbox only."""
import os
import subprocess
import sys
import unittest

from tools.config_transaction_sandbox import OfflineSandbox
from tools.config_writer_lock import LockUnavailable, sandbox_writer_lock


CURRENT = b'[audio_startup]\nprebuffer_before_sonos = true\n'
TEMPLATE = b'[audio_startup]\nprebuffer_before_sonos = true\npcm_timeout_seconds = 45\n'
KEYS = ('audio_startup.pcm_timeout_seconds',)

CHILD = """
import sys
from tools.config_writer_lock import sandbox_writer_lock
with sandbox_writer_lock(int(sys.argv[1])):
    print("LOCKED", flush=True)
    sys.stdin.readline()
"""


@unittest.skipUnless(os.name == 'posix', 'requires POSIX flock and pass_fds')
class CooperativeWriterLockTests(unittest.TestCase):
    def test_two_processes_cannot_apply_concurrently(self):
        with OfflineSandbox() as sandbox:
            original_sha = sandbox.seed(CURRENT)
            proc = subprocess.Popen(
                [sys.executable, '-c', CHILD, str(sandbox._dirfd)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
                pass_fds=(sandbox._dirfd,))
            try:
                self.assertEqual(proc.stdout.readline().strip(), 'LOCKED')
                with self.assertRaises(LockUnavailable):
                    sandbox.apply(TEMPLATE, KEYS, expected_sha256=original_sha)
                self.assertEqual(sandbox.read_current(), CURRENT)
            finally:
                if proc.poll() is None:
                    proc.stdin.write('\n')
                    proc.stdin.flush()
                _out, err = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0, err)
            applied = sandbox.apply(TEMPLATE, KEYS, expected_sha256=original_sha)
            self.assertNotEqual(applied, original_sha)

    def test_local_nested_acquire_refused(self):
        with OfflineSandbox() as sandbox:
            sha = sandbox.seed(CURRENT)
            with sandbox_writer_lock(sandbox._dirfd):
                with self.assertRaises(LockUnavailable):
                    sandbox.apply(TEMPLATE, KEYS, expected_sha256=sha)
            self.assertEqual(sandbox.read_current(), CURRENT)

    def test_lock_inode_persists_across_operations(self):
        with OfflineSandbox() as sandbox:
            sha = sandbox.seed(CURRENT)
            path = sandbox.directory / 'ops01-writer.lock'
            first = path.stat()
            changed = sandbox.apply(TEMPLATE, KEYS, expected_sha256=sha)
            sandbox.rollback(expected_sha256=changed)
            second = path.stat()
            self.assertEqual((first.st_dev, first.st_ino),
                             (second.st_dev, second.st_ino))
            self.assertEqual(second.st_mode & 0o777, 0o600)

    def test_exposed_lock_permissions_block_writes(self):
        with OfflineSandbox() as sandbox:
            sha = sandbox.seed(CURRENT)
            (sandbox.directory / 'ops01-writer.lock').chmod(0o644)
            with self.assertRaises(LockUnavailable):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=sha)
            self.assertEqual(sandbox.read_current(), CURRENT)

    def test_lock_symlink_rejected(self):
        with OfflineSandbox() as sandbox:
            sha = sandbox.seed(CURRENT)
            path = sandbox.directory / 'ops01-writer.lock'
            path.unlink()
            path.symlink_to(sandbox.directory / 'current.toml')
            with self.assertRaises(LockUnavailable):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=sha)
            self.assertEqual(sandbox.read_current(), CURRENT)


if __name__ == '__main__':
    unittest.main()
