"""Offline opt-in OLED/systemd handover checks; no service installation."""
from pathlib import Path
import unittest

from integrations.outputs.oled_status import preserve_display_on_exit

ROOT = Path(__file__).resolve().parents[1]


class ShutdownHandoverSystemdTests(unittest.TestCase):
    def test_preserve_is_disabled_without_opt_in(self):
        self.assertFalse(preserve_display_on_exit(False, {}))
        self.assertFalse(preserve_display_on_exit(False, {'SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT': '0'}))
        self.assertFalse(preserve_display_on_exit(False, {'SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT': 'true'}))

    def test_explicit_flag_or_systemd_environment_enables_preserve(self):
        self.assertTrue(preserve_display_on_exit(True, {}))
        self.assertTrue(preserve_display_on_exit(False, {'SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT': '1'}))

    def test_early_shutdown_unit_waits_for_oled_stop(self):
        unit = (ROOT / 'systemd/sonoscdplayer-shutdown-screen.service').read_text()
        self.assertIn('Before=sonoscdplayer-oled.service', unit)
        self.assertIn('RemainAfterExit=yes', unit)
        self.assertIn('ConditionFileIsExecutable=', unit)
        self.assertIn('ExecStop=-/usr/local/libexec/sonoscdplayer-shutdown-display transition', unit)
        self.assertNotIn('safetounplug', unit.lower())
        self.assertNotIn('WantedBy=multi-user.target', unit)

    def test_late_hook_selects_only_poweroff_and_reboot(self):
        script = (ROOT / 'systemd/sonoscdplayer-final-shutdown-hook').read_text()
        self.assertIn('poweroff) phase=final', script)
        self.assertIn('reboot) phase=reboot', script)
        self.assertIn('*) exit 0', script)
        self.assertNotIn('phase=safe', script)
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            hook = Path(directory) / 'hook.sh'
            bin_dir = Path(directory) / 'bin'
            bin_dir.mkdir()
            log = Path(directory) / 'phases'
            binary = bin_dir / 'renderer'
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "' + str(log) + '"\n')
            binary.chmod(0o755)
            hook.write_text(script.replace('/usr/local/libexec/sonoscdplayer-shutdown-display', str(binary)))
            for action in ('poweroff', 'reboot', 'halt', 'kexec', 'unknown'):
                self.assertEqual(subprocess.run(['sh', str(hook), action], check=False).returncode, 0)
            self.assertEqual(log.read_text().splitlines(), ['final', 'reboot'])

    def test_dropin_does_not_override_installed_renderer_command(self):
        conf = (ROOT / 'systemd/sonoscdplayer-oled.service.d/exit-handover.conf.example').read_text()
        self.assertIn('Environment=SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT=1', conf)
        self.assertNotIn('ExecStart=', conf)
        self.assertNotIn('--ui-target-rpc', conf)


if __name__ == '__main__':
    unittest.main()
