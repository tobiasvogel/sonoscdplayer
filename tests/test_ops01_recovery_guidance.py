"""Read-only OPS-01 recovery guidance on disposable sandbox configurations."""
import errno
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from tools.config_recovery_guidance import recovery_guidance
from tools.config_transaction_sandbox import OfflineSandbox, TransactionRejected

ORIGINAL = (b'[audio_startup]\nprebuffer_before_sonos = false\n'
            b'[service]\nexperimental_safe_screen = true\n')
TEMPLATE = (b'[audio_startup]\nprebuffer_before_sonos = true\n'
            b'pcm_timeout_seconds = 45\n'
            b'[service]\nexperimental_safe_screen = false\n')
KEYS = ('audio_startup.pcm_timeout_seconds',)


class RecoveryGuidanceTests(unittest.TestCase):
    def assert_read_only_report(self, sandbox, expected_condition, expected_step):
        before = {
            p.name: p.read_bytes() for p in sandbox.directory.iterdir()
            if p.is_file()
        }
        report = recovery_guidance(sandbox._dirfd, TEMPLATE)
        after = {
            p.name: p.read_bytes() for p in sandbox.directory.iterdir()
            if p.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(report['condition'], expected_condition)
        self.assertEqual(report['next_step'], expected_step)
        self.assertIs(report['write_authorized'], False)
        self.assertIs(report['restart_authorized'], False)
        self.assertNotIn('experimental_safe_screen', repr(report))
        return report

    def test_unstarted_sandbox_is_indeterminate(self):
        with OfflineSandbox() as sandbox:
            sandbox.seed(ORIGINAL)
            self.assert_read_only_report(sandbox, 'indeterminate', 'manual_review')

    def test_pre_replace_failure_reports_original_intact(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=original,
                              fail_at='before_replace')
            report = self.assert_read_only_report(
                sandbox, 'original_intact', 'inspect_before_retry')
            self.assertEqual((report['phase'], report['configuration']),
                             ('PREPARED', 'not_replaced'))

    def test_post_replace_failure_reports_manual_review(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(TEMPLATE, KEYS, expected_sha256=original,
                              fail_at='after_replace')
            self.assert_read_only_report(
                sandbox, 'replacement_unconfirmed', 'manual_review')

    def test_successful_apply_reports_verified_without_actions(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=original)
            self.assert_read_only_report(
                sandbox, 'candidate_verified', 'no_automatic_action')

    def test_rollback_prepared_with_candidate_reports_uncertain(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            candidate = sandbox.apply(TEMPLATE, KEYS, expected_sha256=original)
            real_replace = os.replace

            def interrupted(src, dst, **kwargs):
                if (src, dst) == ('rollback-staged.toml', 'current.toml'):
                    raise OSError(errno.EIO, 'synthetic rollback failure')
                return real_replace(src, dst, **kwargs)

            with patch('tools.config_transaction_sandbox.os.replace',
                       side_effect=interrupted):
                with self.assertRaises(OSError):
                    sandbox.rollback(expected_sha256=candidate)
            self.assert_read_only_report(
                sandbox, 'rollback_unconfirmed', 'manual_review')

    def test_rollback_prepared_with_original_reports_uncertain(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            candidate = sandbox.apply(TEMPLATE, KEYS, expected_sha256=original)
            real_fsync = os.fsync
            counter = 0

            def interrupted(fd):
                nonlocal counter
                counter += 1
                if counter == 4:
                    raise OSError(errno.EIO, 'synthetic post-restore sync failure')
                return real_fsync(fd)

            with patch('tools.config_transaction_sandbox.os.fsync',
                       side_effect=interrupted):
                with self.assertRaises(OSError):
                    sandbox.rollback(expected_sha256=candidate)
            self.assert_read_only_report(
                sandbox, 'rollback_unconfirmed', 'manual_review')

    def test_complete_rollback_reports_original_restored(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            candidate = sandbox.apply(TEMPLATE, KEYS, expected_sha256=original)
            sandbox.rollback(expected_sha256=candidate)
            self.assert_read_only_report(
                sandbox, 'original_restored', 'no_automatic_action')

    def test_corrupted_phase_returns_redacted_indeterminate(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=original)
            (sandbox.directory / 'transaction-phase.json').write_bytes(
                b'{"phase":"INVALID_PRIVATE_VALUE"}')
            report = self.assert_read_only_report(
                sandbox, 'indeterminate', 'manual_review')
            self.assertIsNone(report['phase'])
            self.assertNotIn('INVALID_PRIVATE_VALUE', repr(report))

    def test_external_change_is_not_reported_as_verified(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=original)
            (sandbox.directory / 'current.toml').write_bytes(
                b'[external]\nlocal = "PRIVATE_TEST_VALUE"\n')
            report = self.assert_read_only_report(
                sandbox, 'indeterminate', 'manual_review')
            self.assertNotIn('PRIVATE_TEST_VALUE', repr(report))

    def test_missing_backup_is_indeterminate(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=original)
            (sandbox.directory / 'backup-original.toml').unlink()
            self.assert_read_only_report(
                sandbox, 'indeterminate', 'manual_review')

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX inherited dirfd')
    def test_fresh_process_can_report_completed_transaction(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            sandbox.apply(TEMPLATE, KEYS, expected_sha256=original)
            sandbox.simulate_process_restart()
            code = (
                'import json,sys;'
                'from tools.config_recovery_guidance import recovery_guidance;'
                'print(json.dumps(recovery_guidance('
                'int(sys.argv[1]),bytes.fromhex(sys.argv[2]))))'
            )
            result = subprocess.run(
                [sys.executable, '-c', code, str(sandbox._dirfd),
                 TEMPLATE.hex()],
                pass_fds=(sandbox._dirfd,),
                stdin=subprocess.DEVNULL, text=True,
                capture_output=True, timeout=10, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['condition'], 'candidate_verified')
            self.assertIs(report['write_authorized'], False)


if __name__ == '__main__':
    unittest.main()
