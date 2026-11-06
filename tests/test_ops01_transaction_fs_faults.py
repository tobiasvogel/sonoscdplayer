"""OPS-01 synthetic filesystem fault-injection tests.

All writes use disposable OfflineSandbox directories. Inject realistic ENOSPC
and EIO at specific syscall boundaries; never touch a live configuration.
Unknown durability is not successful apply or authorized recovery.
"""
import errno
import os
import unittest
from unittest.mock import patch

from tools import config_transaction_sandbox as transaction
from tools.config_recovery_inspect import (
    RecoveryInspectionError, inspect_persisted_phase,
)
from tools.config_transaction_sandbox import OfflineSandbox, TransactionRejected

ORIGINAL = (b'[audio_startup]\nprebuffer_before_sonos = false\n'
            b'[service]\nexperimental_safe_screen = true\n')
REFERENCE = (b'[audio_startup]\nprebuffer_before_sonos = true\n'
             b'pcm_timeout_seconds = 45\n'
             b'[service]\nexperimental_safe_screen = false\n')
KEYS = ('audio_startup.pcm_timeout_seconds',)


class FilesystemFaultTests(unittest.TestCase):
    def _apply_with_fsync_fault(self, sandbox, original_sha, *, occurrence, error):
        real_fsync = os.fsync
        calls = 0

        def inject(fd):
            nonlocal calls
            calls += 1
            if calls == occurrence:
                raise OSError(error, 'synthetic fsync failure')
            return real_fsync(fd)

        with patch.object(transaction.os, 'fsync', side_effect=inject):
            with self.assertRaises(OSError) as raised:
                sandbox.apply(REFERENCE, KEYS, expected_sha256=original_sha)
        self.assertEqual(raised.exception.errno, error)
        self.assertGreaterEqual(calls, occurrence)

    def test_enospc_while_creating_backup_keeps_original(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            create = sandbox._create_synced

            def fail_backup(name, raw):
                if name == 'backup-original.toml':
                    raise OSError(errno.ENOSPC, 'synthetic disk full')
                return create(name, raw)

            with patch.object(sandbox, '_create_synced', side_effect=fail_backup):
                with self.assertRaises(OSError) as raised:
                    sandbox.apply(REFERENCE, KEYS, expected_sha256=original)
            self.assertEqual(raised.exception.errno, errno.ENOSPC)
            self.assertEqual(sandbox.read_current(), ORIGINAL)
            self.assertIsNone(sandbox.transaction_phase())
            self.assertFalse((sandbox.directory / 'transaction-intent.json').exists())

    def test_eio_during_backup_fsync_cannot_publish_candidate(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            self._apply_with_fsync_fault(
                sandbox, original, occurrence=1, error=errno.EIO)
            self.assertEqual(sandbox.read_current(), ORIGINAL)
            self.assertIsNone(sandbox.transaction_phase())
            self.assertTrue((sandbox.directory / 'backup-original.toml').exists())
            with self.assertRaises(TransactionRejected):
                sandbox.apply(REFERENCE, KEYS, expected_sha256=original)

    def test_enospc_during_intent_fsync_keeps_original(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            # backup file/dir, BACKED_UP phase file/dir, candidate, intent
            self._apply_with_fsync_fault(
                sandbox, original, occurrence=6, error=errno.ENOSPC)
            self.assertEqual(sandbox.read_current(), ORIGINAL)
            self.assertEqual(sandbox.transaction_phase(), 'BACKED_UP')
            self.assertTrue((sandbox.directory / 'backup-original.toml').exists())
            with self.assertRaises(RecoveryInspectionError):
                inspect_persisted_phase(sandbox._dirfd, REFERENCE)

    def test_eio_at_replacement_directory_fsync_is_not_success(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            # Two phases and intent are durable before the target replace.
            self._apply_with_fsync_fault(
                sandbox, original, occurrence=10, error=errno.EIO)
            self.assertIn(b'pcm_timeout_seconds = 45', sandbox.read_current())
            self.assertEqual(sandbox.transaction_phase(), 'PREPARED')
            self.assertEqual(inspect_persisted_phase(sandbox._dirfd, REFERENCE),
                             ('PREPARED', 'replacement_verified'))
            with self.assertRaises(TransactionRejected):
                sandbox.apply(REFERENCE, KEYS, expected_sha256=original)

    def test_enospc_during_final_phase_fsync_is_not_success(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            # The final VERIFIED phase update fails before replacing phase JSON.
            self._apply_with_fsync_fault(
                sandbox, original, occurrence=13, error=errno.ENOSPC)
            self.assertIn(b'pcm_timeout_seconds = 45', sandbox.read_current())
            self.assertEqual(sandbox.transaction_phase(), 'REPLACED')
            self.assertEqual(inspect_persisted_phase(sandbox._dirfd, REFERENCE),
                             ('REPLACED', 'replacement_verified'))

    def test_eio_at_target_replace_preserves_original_and_backup(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            real_replace = os.replace

            def inject(src, dst, **kwargs):
                if (src, dst) == ('candidate-staged.toml', 'current.toml'):
                    raise OSError(errno.EIO, 'synthetic replace failure')
                return real_replace(src, dst, **kwargs)

            with patch.object(transaction.os, 'replace', side_effect=inject):
                with self.assertRaises(OSError):
                    sandbox.apply(REFERENCE, KEYS, expected_sha256=original)
            self.assertEqual(sandbox.read_current(), ORIGINAL)
            self.assertEqual(sandbox.transaction_phase(), 'PREPARED')
            self.assertEqual((sandbox.directory / 'backup-original.toml').read_bytes(),
                             ORIGINAL)


if __name__ == '__main__':
    unittest.main()
