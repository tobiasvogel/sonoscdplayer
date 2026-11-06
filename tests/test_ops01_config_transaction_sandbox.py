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

    def test_interrupted_after_replace_can_be_verified_and_rolled_back(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old,
                              fail_at='after_replace')
            changed = sandbox.read_current()
            self.assertIn(b'pcm_timeout_seconds = 45', changed)
            self.assertEqual(sandbox.recover_interrupted(
                TEMPLATE, KEYS, expected_original_sha256=old),
                'replacement_verified')
            self.assertEqual(sandbox.rollback(expected_sha256=digest(changed)), old)
            self.assertEqual(sandbox.read_current(), CURRENT)

    def test_before_replace_recovery_is_read_only(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old,
                              fail_at='before_replace')
            self.assertEqual(sandbox.recover_interrupted(
                TEMPLATE, KEYS, expected_original_sha256=old), 'not_replaced')
            self.assertEqual(sandbox.read_current(), CURRENT)

    def test_interrupted_recovery_refuses_drifted_target(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old,
                              fail_at='after_replace')
            target = sandbox.directory / 'current.toml'
            target.write_bytes(b'[external]\\nsecret = "MUST_PRESERVE"\\n')
            with self.assertRaises(TransactionRejected):
                sandbox.recover_interrupted(
                    TEMPLATE, KEYS, expected_original_sha256=old)
            self.assertEqual(target.read_bytes(),
                             b'[external]\\nsecret = "MUST_PRESERVE"\\n')

    def test_interrupted_recovery_refuses_wrong_orig_checksum(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old,
                              fail_at='after_replace')
            with self.assertRaises(TransactionRejected):
                sandbox.recover_interrupted(
                    TEMPLATE, KEYS, expected_original_sha256='0' * 64)

    def test_interrupted_recovery_refuses_wrong_template(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old,
                              fail_at='after_replace')
            wrong = TEMPLATE.replace(b'pcm_timeout_seconds = 45',
                                     b'pcm_timeout_seconds = 90')
            with self.assertRaises(ValueError):
                sandbox.recover_interrupted(
                    wrong, KEYS, expected_original_sha256=old)

    def test_persisted_intent_survives_volatile_state_loss(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            applied = sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            journal = sandbox.directory / 'transaction-intent.json'
            self.assertTrue(journal.is_file())
            self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b'PRIVATE_TEST_VALUE', journal.read_bytes())
            self.assertNotIn(b'experimental_safe_screen', journal.read_bytes())
            sandbox.simulate_process_restart()
            self.assertEqual(sandbox.inspect_after_restart(TEMPLATE),
                             'replacement_verified')
            self.assertEqual(sandbox.arm_rollback_after_restart(TEMPLATE),
                             applied)
            self.assertEqual(sandbox.rollback(expected_sha256=applied), old)
            self.assertEqual(sandbox.read_current(), CURRENT)

    def test_persisted_intent_classifies_pre_replace_interruption(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=old,
                              fail_at='before_replace')
            sandbox.simulate_process_restart()
            self.assertEqual(sandbox.inspect_after_restart(TEMPLATE),
                             'not_replaced')
            with self.assertRaises(TransactionRejected):
                sandbox.arm_rollback_after_restart(TEMPLATE)
            self.assertEqual(sandbox.read_current(), CURRENT)

    def test_restart_refuses_changed_template(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            sandbox.simulate_process_restart()
            with self.assertRaises(TransactionRejected):
                sandbox.inspect_after_restart(TEMPLATE + b'\\n# changed\\n')

    def test_restart_refuses_tampered_journal(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            sandbox.simulate_process_restart()
            (sandbox.directory / 'transaction-intent.json').write_bytes(
                b'{"version":1,"original_sha256":"fake"}')
            with self.assertRaises(TransactionRejected):
                sandbox.inspect_after_restart(TEMPLATE)
            self.assertIn(b'pcm_timeout_seconds = 45', sandbox.read_current())

    def test_restart_refuses_modified_current_content(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(CURRENT)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=old)
            sandbox.simulate_process_restart()
            (sandbox.directory / 'current.toml').write_bytes(
                b'[external]\\nvalue = "preserve"\\n')
            with self.assertRaises(TransactionRejected):
                sandbox.arm_rollback_after_restart(TEMPLATE)
            self.assertIn(b'preserve', sandbox.read_current())

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
