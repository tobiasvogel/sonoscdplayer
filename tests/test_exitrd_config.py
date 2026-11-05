"""Configuration gate tests: all activation paths mocked; never mount or reboot."""
import contextlib
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import exitrd_config as config  # noqa: E402


class ExitrdConfigTests(unittest.TestCase):
    def config_file(self, text):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / 'shutdown.ini'
        path.write_text(text)
        self.addCleanup(temp.cleanup)
        return path

    def test_shipped_default_is_disabled(self):
        self.assertFalse(config.read_enabled())
        self.assertIn('experimental_safe_screen = false', config.CONFIG.read_text())

    def test_parser_rejects_invalid_and_extra_keys(self):
        for data in ('', '[shutdown]\nexperimental_safe_screen = yes\n',
                     '[shutdown]\nexperimental_safe_screen = true\nextra = x\n',
                     '[other]\nexperimental_safe_screen = true\n',
                     '[shutdown]\nexperimental_safe_screen = true\n'
                     '[shutdown]\nexperimental_safe_screen = false\n'):
            with self.subTest(data=data):
                with self.assertRaises(config.ConfigError):
                    config.read_enabled(self.config_file(data))

    def test_config_true_is_explicit_only(self):
        path = self.config_file('[shutdown]\nexperimental_safe_screen = true\n')
        self.assertTrue(config.read_enabled(path))

    def test_status_is_read_only_even_if_enabled(self):
        with patch.object(config, 'read_enabled', return_value=True), \
             patch.object(config.exitrd_once, 'precheck') as pre, \
             patch.object(config.exitrd_once, 'arm_once') as arm, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(config.main([]), 0)
        self.assertIn('READ-ONLY', output.getvalue())
        pre.assert_not_called()
        arm.assert_not_called()

    def test_disabled_config_blocks_activation(self):
        with patch.object(config, 'read_enabled', return_value=False), \
             patch.object(config.exitrd_once, 'precheck') as pre, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(config.main(['--activate-once']), 1)
        pre.assert_not_called()

    def test_both_acknowledgements_and_digest_are_mandatory(self):
        attempts = [
            ['--activate-once'],
            ['--activate-once', '--acknowledge-risk', config.RISK_ACK],
            ['--activate-once', '--acknowledge', config.exitrd_once.ACK],
            ['--activate-once', '--acknowledge-risk', config.RISK_ACK,
             '--acknowledge', config.exitrd_once.ACK],
        ]
        for args in attempts:
            with self.subTest(args=args), \
                 patch.object(config, 'read_enabled', return_value=True), \
                 patch.object(config, 'verify_experimental_binary') as verify, \
                 contextlib.redirect_stderr(io.StringIO()), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(config.main(args), 1)
                verify.assert_not_called()

    def test_binary_mode_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'shutdown'
            binary.write_text('placeholder')
            with patch('subprocess.run', return_value=subprocess.CompletedProcess(
                    [], 0, stdout='exitrd: offline check safe-approved=0 diagnostic=0 '
                                  'unverified-safe=0 delay-seconds=0', stderr='')):
                with self.assertRaisesRegex(config.ConfigError, 'does not identify'):
                    config.verify_experimental_binary(binary)

    def test_verified_activation_delegates_exact_binary(self):
        data = b'static-test-binary'
        digest = hashlib.sha256(data).hexdigest()
        args = ['--activate-once', '--expected-sha256', digest,
                '--acknowledge-risk', config.RISK_ACK,
                '--acknowledge', config.exitrd_once.ACK]
        with patch.object(config, 'read_enabled', return_value=True), \
             patch.object(config, 'verify_experimental_binary') as verified, \
             patch.object(config.exitrd_once, 'precheck',
                          return_value=(data, digest, {})) as pre, \
             patch.object(config.exitrd_once, 'arm_once') as arm, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(config.main(args), 0)
        verified.assert_called_once_with()
        pre.assert_called_once_with(config.EXPERIMENTAL, digest, activating=True)
        arm.assert_called_once_with(data, digest, {})

    def test_corrupted_config_cannot_block_explicit_rollback(self):
        with patch.object(config.exitrd_once, 'disarm') as disarm, \
             patch.object(config, 'read_enabled') as read:
            self.assertEqual(config.main(['--deactivate']), 0)
        disarm.assert_called_once_with()
        read.assert_not_called()

    def test_no_automatic_staging_or_poweroff(self):
        source = (ROOT / 'tools/exitrd_config.py').read_text()
        self.assertNotIn('systemctl', source)
        self.assertNotIn('SYS_reboot', source)
        self.assertIn('if not args.activate_once:', source)
        self.assertIn('exitrd_once.arm_once(payload, digest, preserved)', source)


if __name__ == '__main__':
    unittest.main()
