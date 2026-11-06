"""OPS-01 on-disk rehearsal tests: only auto-created disposable directories."""
import os
import unittest

from tools.config_transaction_sandbox import OfflineSandbox, TransactionRejected, digest

CURRENT = (b'[audio_startup]\nprebuffer_before_sonos = false\n'
           b'\n[service]\nexperimental_safe_screen = true\n'
           b'\n[led_outputs.ambient]\nenabled = false\n'
           b'local_extra = "PRIVATE_TEST_VALUE"\n')
TEMPLATE = (b'[audio_startup]\nprebuffer_before_sonos = true\n'
            b'pcm_timeout_seconds = 45\n'
            b'\n[service]\nexperimental_safe_screen = false\n'
            b'\n[led_outputs.ambient]\nenabled = true\n'
            b'timeout_ms = 150\n')
KEYS = ('audio_startup.pcm_timeout_seconds',)


class SandboxTransactionTests(unittest.TestCase):
    def test_apply_backup_then_rollback_restores_exact_bytes(self):
        with OfflineSandbox() as sandbox:
            original_sha = sandbox.seed(CURRENT)
            applied_sha = sandbox.apply(TEMPLATE, KEYS, expected_sha256=original_sha)
            self.assertNotEqual(applied_sha, original_sha)
            self.assertIn(b'pcm_timeout_seconds = 45', sandbox.read_current())
            self.assertIn(b'experimental_safe_screen = true', sandbox.read_current())
            self.assertIn(b'PRIVATE_TEST_VALUE', sandbox.read_current())
            backup = sandbox.directory / 'backup-original.toml'
            self.assertEqual(backup.read_bytes(), CURRENT)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(sandbox.rollback(expected_sha256=applied_sha), original_sha)
            self.assertEqual(sandbox.read_current(), CURRENT)

    def test_stale_apply_checksum_rejected_without_backup(self):
        with OfflineSandbox() as sandbox:
            sandbox.seed(CURRENT)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256='0' * 64)
            self.assertEqual(sandbox.read_current(), CURRENT)
            self.assertFalse((sandbox.directory / 'backup-original.toml').exists())

    def test_stale_rollback_does_not_destroy_modified_config(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            new = sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            target = sandbox.directory / 'current.toml'
            target.write_bytes(b'[custom]\nvalue = "externally modified"\n')
            with self.assertRaises(TransactionRejected):
                sandbox.rollback(expected_sha256=new)
            self.assertIn(b'externally modified', target.read_bytes())
            self.assertEqual((sandbox.directory / 'backup-original.toml').read_bytes(), CURRENT)

    def test_corrupt_backup_refuses_rollback(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            new = sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            (sandbox.directory / 'backup-original.toml').write_bytes(b'CORRUPTED')
            with self.assertRaises(TransactionRejected):
                sandbox.rollback(expected_sha256=new)
            self.assertIn(b'pcm_timeout_seconds = 45', sandbox.read_current())

    def test_fault_before_replace_preserves_original_and_backup(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old,
                              fail_at='before_replace')
            self.assertEqual(sandbox.read_current(), CURRENT)
            self.assertEqual((sandbox.directory / 'backup-original.toml').read_bytes(), CURRENT)
            self.assertFalse((sandbox.directory / 'candidate-staged.toml').exists())

    def test_symlink_target_rejected(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            target = sandbox.directory / 'current.toml'
            target.unlink()
            target.symlink_to(sandbox.directory / 'outside.toml')
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)

    def test_hardlink_target_rejected(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            os.link(sandbox.directory / 'current.toml',
                    sandbox.directory / 'second-link.toml')
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)

    def test_disallows_second_apply_without_prior_restore(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            new = sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=new)

    def test_no_explicit_config_path_or_prod_service_access(self):
        with OfflineSandbox() as sandbox:
            self.assertEqual(sandbox.seed(CURRENT), digest(CURRENT))
            self.assertTrue(sandbox.directory.name.startswith('ops01-offline-'))
            self.assertNotIn('/home/pi/sonoscdplayer', str(sandbox.directory))
            # No production path is accepted by seed/apply/rollback.

    def test_closed_sandbox_refuses_operations(self):
        sandbox = OfflineSandbox()
        sandbox.seed(CURRENT)
        sandbox.close()
        with self.assertRaises(TransactionRejected):
            sandbox.read_current()


if __name__ == '__main__':
    unittest.main()
