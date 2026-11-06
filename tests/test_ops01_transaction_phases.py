"""OPS-01 durable phase journal tests on disposable synthetic sandboxes."""
import json
import os
import unittest

from tools.config_recovery_inspect import (
    RecoveryInspectionError, inspect_persisted_phase,
)
from tools.config_transaction_phase import PhaseRejected, persist_phase, read_phase
from tools.config_transaction_sandbox import OfflineSandbox, TransactionRejected

ORIGINAL = (b'[audio_startup]\nprebuffer_before_sonos = false\n'
            b'[service]\nexperimental_safe_screen = true\n')
REFERENCE = (b'[audio_startup]\nprebuffer_before_sonos = true\n'
             b'pcm_timeout_seconds = 45\n'
             b'[service]\nexperimental_safe_screen = false\n')
KEYS = ('audio_startup.pcm_timeout_seconds',)


class DurableTransactionPhaseTests(unittest.TestCase):
    def test_successful_apply_and_rollback_are_durable(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            self.assertIsNone(sandbox.transaction_phase())
            applied = sandbox.apply(REFERENCE, KEYS, expected_sha256=original)
            self.assertEqual(sandbox.transaction_phase(), 'VERIFIED')
            self.assertEqual(inspect_persisted_phase(sandbox._dirfd, REFERENCE),
                             ('VERIFIED', 'replacement_verified'))
            phase_file = sandbox.directory / 'transaction-phase.json'
            self.assertEqual(phase_file.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b'experimental_safe_screen', phase_file.read_bytes())
            self.assertEqual(sandbox.rollback(expected_sha256=applied), original)
            sandbox.simulate_process_restart()
            self.assertEqual(inspect_persisted_phase(sandbox._dirfd, REFERENCE),
                             ('ROLLED_BACK', 'not_replaced'))

    def test_interrupted_before_replace_remains_prepared(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(REFERENCE, KEYS, expected_sha256=original,
                              fail_at='before_replace')
            sandbox.simulate_process_restart()
            self.assertEqual(inspect_persisted_phase(sandbox._dirfd, REFERENCE),
                             ('PREPARED', 'not_replaced'))
            self.assertEqual(sandbox.read_current(), ORIGINAL)

    def test_interrupted_after_replace_remains_replaced(self):
        with OfflineSandbox() as sandbox:
            original = sandbox.seed(ORIGINAL)
            with self.assertRaises(TransactionRejected):
                sandbox.apply(REFERENCE, KEYS, expected_sha256=original,
                              fail_at='after_replace')
            sandbox.simulate_process_restart()
            self.assertEqual(inspect_persisted_phase(sandbox._dirfd, REFERENCE),
                             ('REPLACED', 'replacement_verified'))

    def test_invalid_or_repeated_transitions_refused(self):
        with OfflineSandbox() as sandbox:
            with self.assertRaises(PhaseRejected):
                persist_phase(sandbox._dirfd, 'VERIFIED')
            persist_phase(sandbox._dirfd, 'BACKED_UP')
            with self.assertRaises(PhaseRejected):
                persist_phase(sandbox._dirfd, 'BACKED_UP')
            self.assertEqual(read_phase(sandbox._dirfd), 'BACKED_UP')
            with self.assertRaises(RecoveryInspectionError):
                inspect_persisted_phase(sandbox._dirfd, REFERENCE)

    def test_corrupt_phase_does_not_authorize_recovery(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(ORIGINAL)
            sandbox.apply(REFERENCE, KEYS, expected_sha256=old)
            phase_file = sandbox.directory / 'transaction-phase.json'
            phase_file.write_text('{"version":1,"phase":"UNKNOWN"}')
            with self.assertRaises(RecoveryInspectionError):
                inspect_persisted_phase(sandbox._dirfd, REFERENCE)
            self.assertNotEqual(sandbox.read_current(), ORIGINAL)

    def test_phase_cannot_contradict_current_bytes(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(ORIGINAL)
            sandbox.apply(REFERENCE, KEYS, expected_sha256=old)
            path = sandbox.directory / 'transaction-phase.json'
            path.write_bytes(json.dumps({'version': 1, 'phase': 'ROLLED_BACK'}).encode())
            with self.assertRaisesRegex(RecoveryInspectionError, 'contradicts'):
                inspect_persisted_phase(sandbox._dirfd, REFERENCE)

    def test_stale_phase_stage_prevents_inspection_and_writes(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(ORIGINAL)
            sandbox.apply(REFERENCE, KEYS, expected_sha256=old)
            staged = sandbox.directory / 'transaction-phase-staged.json'
            staged.write_bytes(b'incomplete')
            with self.assertRaises(RecoveryInspectionError):
                inspect_persisted_phase(sandbox._dirfd, REFERENCE)
            with self.assertRaises(FileExistsError):
                persist_phase(sandbox._dirfd, 'ROLLBACK_PREPARED')
            self.assertEqual(sandbox.transaction_phase(), 'VERIFIED')

    def test_insecure_phase_permissions_rejected(self):
        with OfflineSandbox() as sandbox:
            old = sandbox.seed(ORIGINAL)
            sandbox.apply(REFERENCE, KEYS, expected_sha256=old)
            path = sandbox.directory / 'transaction-phase.json'
            path.chmod(0o644)
            with self.assertRaises(RecoveryInspectionError):
                inspect_persisted_phase(sandbox._dirfd, REFERENCE)


if __name__ == '__main__':
    unittest.main()
