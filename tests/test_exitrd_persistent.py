"""Offline persistent exitrd tests; no systemctl enable, mounts or root writes."""
from pathlib import Path
import contextlib
import io
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import install_exitrd_persistent as installer
import exitrd_autostage as stager


class PersistentExitrdTests(unittest.TestCase):
    def test_installed_unit_rearms_at_boot_and_not_during_shutdown(self):
        unit = (ROOT / 'systemd/sonoscdplayer-exitrd.service').read_text()
        self.assertIn('WantedBy=multi-user.target', unit)
        self.assertIn('Type=oneshot', unit)
        self.assertIn('After=local-fs.target', unit)
        self.assertIn('exitrd_autostage.py', unit)
        self.assertNotIn('ExecStop=', unit)
        self.assertNotIn('RemainAfterExit=yes', unit)
        self.assertNotIn('poweroff', unit)
        self.assertNotIn('reboot', unit)

    def test_shipped_template_defaults_off_and_runtime_is_boolean(self):
        self.assertFalse(stager.enabled(ROOT / 'config/player.example.toml'))
        self.assertIsInstance(stager.enabled(ROOT / 'src/config.toml'), bool)

    def test_autostage_does_not_arm_when_config_disabled(self):
        with patch.object(stager, 'enabled', return_value=False), \
             patch.object(stager, 'verify_installed') as verified, \
             patch.object(stager.exitrd_once, 'arm_once') as armed, \
             patch.object(stager.os, 'geteuid', return_value=0), \
             contextlib.redirect_stdout(io.StringIO()):
            stager.stage(Path('/invalid'), Path('/invalid'), Path('/invalid'))
        verified.assert_not_called()
        armed.assert_not_called()

    def test_autostage_rejects_wrong_toml_type(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'player.toml'
            file.write_text('[service]\nexperimental_safe_screen = "true"\n')
            with self.assertRaises(stager.StageError):
                stager.enabled(file)

    def test_verified_autostage_delegates_to_existing_preflight(self):
        with patch.object(stager, 'enabled', return_value=True), \
             patch.object(stager, 'verify_installed', return_value='a' * 64), \
             patch.object(stager.exitrd_once, 'precheck',
                          return_value=(b'binary', 'a' * 64, {})) as pre, \
             patch.object(stager.exitrd_once, 'arm_once') as arm, \
             patch.object(stager.os, 'geteuid', return_value=0), \
             contextlib.redirect_stdout(io.StringIO()):
            stager.stage(Path('config.toml'), Path('shutdown'), Path('checksum'))
        pre.assert_called_once_with(Path('shutdown'), 'a' * 64, activating=True)
        arm.assert_called_once_with(b'binary', 'a' * 64, {})

    def test_persistent_config_edit_preserves_other_sections(self):
        original = '[streaming]\nmax_buffer_mib = 16\n[service]\nshutdown_policy = "stop_owned"\n[led_outputs]\npower_pin = 12\n'
        enabled = installer.verified_config_text(original, True)
        self.assertIn('experimental_safe_screen = true', enabled)
        self.assertIn('max_buffer_mib = 16', enabled)
        self.assertIn('power_pin = 12', enabled)
        disabled = installer.verified_config_text(enabled, False)
        self.assertIn('experimental_safe_screen = false', disabled)
        self.assertEqual(disabled.count('experimental_safe_screen'), 1)

    def test_config_bad_values_and_missing_service_refused(self):
        for source in ('[streaming]\nx = 1\n',
                       '[service]\nexperimental_safe_screen = "yes"\n',
                       '[service]\nexperimental_safe_screen = 3\n'):
            with self.subTest(source=source):
                with self.assertRaises(installer.InstallError):
                    installer.verified_config_text(source, True)

    def test_management_requires_interactive_consent(self):
        source = (ROOT / 'tools/manage_experimental_exitrd.sh').read_text()
        self.assertIn('--install', source)
        self.assertIn('--uninstall', source)
        self.assertIn('YES-I-ACCEPT', source)
        self.assertIn('install_exitrd_persistent.py', source)
        self.assertNotIn('systemctl poweroff', source)

    def test_installer_never_replaces_existing_live_exitrd(self):
        source = (ROOT / 'tools/install_exitrd_persistent.py').read_text()
        self.assertIn('precheck(EXPERIMENTAL, expected, activating=True)', source)
        self.assertIn("command('systemctl', 'enable', '--now', NAME)", source)
        self.assertIn("command('systemctl', 'disable', NAME)", source)
        self.assertNotIn("poweroff", source.lower().replace("no poweroff", ""))

if __name__ == '__main__':
    unittest.main()
