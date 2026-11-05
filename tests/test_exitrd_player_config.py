"""Fail-closed tests of the existing player TOML shutdown opt-in.

No live mounts, no poweroff and no real exitrd arming.
"""
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
sys.path.insert(0, str(ROOT / "tools"))
import exitrd_player_config as cfg  # noqa: E402


class ExitrdPlayerConfigTests(unittest.TestCase):
    def fixture(self, content):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        file = Path(temp.name) / "player.toml"
        file.write_text(content)
        return file

    def test_existing_example_defaults_to_disabled(self):
        self.assertFalse(cfg.enabled_from_player_config(ROOT / "config/player.example.toml"))

    def test_missing_option_defaults_off(self):
        self.assertFalse(cfg.enabled_from_player_config(self.fixture('[service]\nshutdown_policy = "stop_owned"\n')))

    def test_existing_service_options_are_accepted(self):
        file = self.fixture('[service]\nshutdown_policy = "stop_owned"\nexperimental_safe_screen = true\n')
        self.assertTrue(cfg.enabled_from_player_config(file))

    def test_invalid_values_and_files_fail_closed(self):
        for data in ('[service]\nexperimental_safe_screen = "true"\n',
                     '[service]\nexperimental_safe_screen = 1\n',
                     '[service]\nexperimental_safe_screen = true\n'
                     'experimental_safe_screen = false\n',
                     '[service\nexperimental_safe_screen = true\n'):
            with self.subTest(data=data):
                with self.assertRaises(cfg.ConfigurationError):
                    cfg.enabled_from_player_config(self.fixture(data))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(cfg.ConfigurationError):
                cfg.enabled_from_player_config(Path(temp) / 'missing.toml')
            target = Path(temp) / 'real.toml'
            target.write_text('[service]\nexperimental_safe_screen = true\n')
            link = Path(temp) / 'link.toml'
            link.symlink_to(target)
            with self.assertRaises(cfg.ConfigurationError):
                cfg.enabled_from_player_config(link)

    def test_status_cannot_arm_even_when_enabled(self):
        file = self.fixture('[service]\nexperimental_safe_screen = true\n')
        with patch.object(cfg.exitrd_once, 'precheck') as pre, \
             patch.object(cfg.exitrd_once, 'arm_once') as arm, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cfg.main(['--config', str(file)]), 0)
        self.assertIn('READ-ONLY', output.getvalue())
        pre.assert_not_called()
        arm.assert_not_called()

    def test_disabled_blocks_activation_before_binary_check(self):
        file = self.fixture('[service]\nexperimental_safe_screen = false\n')
        with patch.object(cfg, 'verify_binary') as verify, \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cfg.main(['--config', str(file), '--activate-once']), 1)
        verify.assert_not_called()

    def test_two_acknowledgements_and_sha_required(self):
        file = self.fixture('[service]\nexperimental_safe_screen = true\n')
        for extra in (
            [],
            ['--acknowledge', cfg.exitrd_once.ACK],
            ['--acknowledge-risk', cfg.RISK_ACK],
            ['--acknowledge', cfg.exitrd_once.ACK, '--acknowledge-risk', cfg.RISK_ACK],
        ):
            with self.subTest(extra=extra), \
                 patch.object(cfg, 'verify_binary') as verify, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cfg.main(['--config', str(file), '--activate-once', *extra]), 1)
            verify.assert_not_called()

    def test_mode_check_rejects_wrong_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / 'shutdown'
            executable.write_text('mock')
            with patch.object(cfg, 'EXPERIMENTAL', executable), \
                 patch('subprocess.run', return_value=subprocess.CompletedProcess(
                     [], 0, stdout='safe-approved=0 diagnostic=0 unverified-safe=0 delay-seconds=0')):
                with self.assertRaises(cfg.ConfigurationError):
                    cfg.verify_binary()

    def test_approved_activation_delegates_to_one_boot_helper(self):
        file = self.fixture('[service]\nexperimental_safe_screen = true\n')
        data = b'test-static-binary'
        digest = hashlib.sha256(data).hexdigest()
        args = ['--config', str(file), '--activate-once',
                '--expected-sha256', digest,
                '--acknowledge', cfg.exitrd_once.ACK,
                '--acknowledge-risk', cfg.RISK_ACK]
        with patch.object(cfg, 'verify_binary') as verify, \
             patch.object(cfg.exitrd_once, 'precheck',
                          return_value=(data, digest, {})) as pre, \
             patch.object(cfg.exitrd_once, 'arm_once') as arm, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cfg.main(args), 0)
        verify.assert_called_once_with()
        pre.assert_called_once_with(cfg.EXPERIMENTAL, digest, activating=True)
        arm.assert_called_once_with(data, digest, {})

    def test_disarm_is_available_even_when_config_broken(self):
        with patch.object(cfg.exitrd_once, 'disarm') as disarm:
            self.assertEqual(cfg.main(['--config', '/nonexistent/player.toml', '--deactivate']), 0)
        disarm.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
