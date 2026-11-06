"""Real child-process termination during an OPS-01 *sandbox-only* transaction.

The parent creates/owns a disposable TemporaryDirectory. Only the forked child
can perform apply, and it is killed at an exact filesystem boundary. The
parent independently inspects the on-disk intent and original/candidate.
There is no way to specify a production path or contact a service.
"""
import os
import signal
import unittest

from tools.config_transaction_sandbox import (
    OfflineSandbox, TransactionRejected, digest,
)

CURRENT = (b'[audio_startup]\nprebuffer_before_sonos = false\n'
           b'\n[service]\nexperimental_safe_screen = true\n')
TEMPLATE = (b'[audio_startup]\nprebuffer_before_sonos = true\n'
            b'pcm_timeout_seconds = 45\n'
            b'\n[service]\nexperimental_safe_screen = false\n')
KEYS = ('audio_startup.pcm_timeout_seconds',)


@unittest.skipUnless(hasattr(os, 'fork') and hasattr(os, 'waitpid'),
                     'requires POSIX fork and waitpid')
class KilledProcessRecoveryTests(unittest.TestCase):
    def _kill_child_at_replace(self, sandbox, original_sha, *, after):
        # This forks only a synthetic, already-owned sandbox. The parent owns
        # cleanup and cannot be killed by the deliberately injected SIGKILL.
        pid = os.fork()
        if pid == 0:
            try:
                real_replace = os.replace

                def intercepted_replace(src, dst, **kwargs):
                    # The durable phase journal also uses atomic replaces.
                    # Allow those to complete normally; kill ONLY at the
                    # actual configuration exchange boundary.
                    if src != 'candidate-staged.toml' or dst != 'current.toml':
                        return real_replace(src, dst, **kwargs)
                    if after:
                        real_replace(src, dst, **kwargs)
                        os.fsync(sandbox._dirfd)
                    os.kill(os.getpid(), signal.SIGKILL)
                    os._exit(75)

                os.replace = intercepted_replace
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=original_sha)
            except BaseException:
                os._exit(76)
            os._exit(77)

        completed_pid, status = os.waitpid(pid, 0)
        self.assertEqual(completed_pid, pid)
        self.assertTrue(os.WIFSIGNALED(status), status)
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)

    def test_killed_before_atomic_replace_preserves_original(self):
        with OfflineSandbox() as sandbox:
            original_sha = sandbox.seed(CURRENT)
            self._kill_child_at_replace(sandbox, original_sha, after=False)
            sandbox.simulate_process_restart()
            self.assertEqual(sandbox.inspect_after_restart(TEMPLATE),
                             'not_replaced')
            self.assertEqual(sandbox.read_current(), CURRENT)
            self.assertEqual((sandbox.directory / 'backup-original.toml').read_bytes(),
                             CURRENT)

    def test_killed_after_atomic_replace_allows_verified_rollback(self):
        with OfflineSandbox() as sandbox:
            original_sha = sandbox.seed(CURRENT)
            self._kill_child_at_replace(sandbox, original_sha, after=True)
            sandbox.simulate_process_restart()
            replaced = sandbox.read_current()
            self.assertIn(b'pcm_timeout_seconds = 45', replaced)
            self.assertEqual(sandbox.inspect_after_restart(TEMPLATE),
                             'replacement_verified')
            applied_sha = sandbox.arm_rollback_after_restart(TEMPLATE)
            self.assertEqual(applied_sha, digest(replaced))
            self.assertEqual(sandbox.rollback(expected_sha256=applied_sha),
                             original_sha)
            self.assertEqual(sandbox.read_current(), CURRENT)

    def test_killed_replacement_rejects_post_crash_external_change(self):
        with OfflineSandbox() as sandbox:
            original_sha = sandbox.seed(CURRENT)
            self._kill_child_at_replace(sandbox, original_sha, after=True)
            sandbox.simulate_process_restart()
            path = sandbox.directory / 'current.toml'
            changed = b'[local]\ncustom = "DO_NOT_OVERWRITE"\n'
            path.write_bytes(changed)
            with self.assertRaises(TransactionRejected):
                sandbox.arm_rollback_after_restart(TEMPLATE)
            self.assertEqual(path.read_bytes(), changed)


if __name__ == '__main__':
    unittest.main()
