"""Offline checks for the one-time post-merge systemd unit preparation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.render_runtime_units import POLKIT_RULE, UNITS, render, main


ROOT = Path(__file__).resolve().parents[1]


class RuntimeUnitRenderTests(unittest.TestCase):
    def setUp(self):
        self.inputs = dict(
            checkout='/opt/example/src',
            python_venv='/opt/example/.venv/bin/python',
            user='example',
        )
        env = mock.patch.dict(os.environ, {'SONOSCDPLAYER_CONFIG_DIR': '/etc/example'})
        env.start()
        self.addCleanup(env.stop)

    def test_renders_four_complete_units_and_preserves_safety_controls(self):
        units = render(ROOT / 'systemd', **self.inputs)
        self.assertEqual(set(units), set(UNITS) | {POLKIT_RULE})
        for name in UNITS:
            content = units[name]
            self.assertIn('WorkingDirectory=/opt/example/src', content)
            self.assertIn('PartOf=sonoscdplayer.target', content)
            self.assertIn('ExecStart=', content)
            self.assertNotIn('preview only', content.lower())
            self.assertNotIn('sonoscdplayer-inp04', content)
            self.assertIn('User=example\n', content)
            self.assertNotIn('@SONOSCDPLAYER_', content)
            self.assertNotIn('#@', content)
        player = units['sonoscdplayer-player.service']
        self.assertIn('RuntimeDirectory=sonoscdplayer\n', player)
        self.assertIn('RuntimeDirectoryMode=0700', player)
        self.assertIn('UMask=0077', player)
        self.assertIn('Restart=no', player)
        self.assertIn('--config /etc/example/player.toml', player)
        led = units['sonoscdplayer-led.service']
        self.assertIn('ExecStart=/usr/bin/python3 ', led)
        self.assertIn('Conflicts=sonoscdplayer-early-power-led.service', led)
        oled = units['sonoscdplayer-oled.service']
        self.assertIn('RuntimeDirectory=sonoscdplayer-ui', oled)
        self.assertIn('RuntimeDirectoryMode=0700', oled)
        self.assertIn('--oled-recover-on-error --ui-target-rpc', oled)
        self.assertIn('--display-config /etc/example/player.toml', oled)
        self.assertIn('--ui-config /etc/example/ui.toml', oled)
        self.assertNotIn('--adopt-boot-splash', oled)
        buttons = units['sonoscdplayer-buttons.service']
        self.assertIn('After=local-fs.target sonoscdplayer-oled.service', buttons)
        self.assertIn('--config /etc/example/interface.toml', buttons)
        self.assertIn('--execute --selected-target-execute', buttons)
        self.assertNotIn('[Install]', buttons)

    def test_optional_switches_are_enabled_by_default(self):
        units = render(ROOT / 'systemd', **self.inputs)
        self.assertIn('--execute --selected-target-execute --power-actions '
                      '--drive-activity-events --connect-gestures',
                      units['sonoscdplayer-buttons.service'])
        self.assertIn('--ui-target-rpc --ui-power-actions',
                      units['sonoscdplayer-oled.service'])
        rule = units[POLKIT_RULE]
        self.assertIn('subject.user === "example"', rule)
        self.assertNotIn('@SONOSCDPLAYER_', rule)

    def test_each_optional_switch_can_be_disabled(self):
        units = render(ROOT / 'systemd', power_actions=False,
                       drive_activity_events=False, connect_gestures=False,
                       **self.inputs)
        buttons = units['sonoscdplayer-buttons.service']
        for flag in ('--power-actions', '--drive-activity-events', '--connect-gestures'):
            self.assertNotIn(flag, buttons)
        self.assertNotIn('--ui-power-actions', units['sonoscdplayer-oled.service'])
        self.assertNotIn(POLKIT_RULE, units)

    def test_rejects_root_or_malformed_service_user(self):
        for invalid in ('root', '', 'Pi', 'a b', 'x;rm', '../x'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    render(ROOT / 'systemd', **dict(self.inputs, user=invalid))

    def test_unknown_placeholder_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            for name in UNITS:
                (source / name).write_text(
                    (ROOT / 'systemd' / name).read_text(encoding='utf-8'),
                    encoding='utf-8')
            player = source / UNITS[0]
            player.write_text(player.read_text(encoding='utf-8') +
                              'Environment=X=@SONOSCDPLAYER_UNKNOWN@\n',
                              encoding='utf-8')
            with self.assertRaises(ValueError):
                render(source, **self.inputs)

    def test_rejects_relative_or_ambiguous_paths(self):
        for invalid in ('relative/src', '/bad path', '/bad\tpath'):
            with self.subTest(invalid=invalid):
                options = dict(self.inputs, checkout=invalid)
                with self.assertRaises(ValueError):
                    render(ROOT / 'systemd', **options)

    def test_only_writes_requested_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'staged'
            argv = ['--repo-root', str(ROOT), '--output-dir', str(out)]
            for name, value in (
                ('--checkout', self.inputs['checkout']),
                ('--python-venv', self.inputs['python_venv']),
                ('--service-user', self.inputs['user']),
            ):
                argv.extend((name, value))
            main(argv + ['--no-power-actions'])
            self.assertEqual({p.name for p in out.iterdir()}, set(UNITS))
            main(argv)
            self.assertEqual({p.name for p in out.iterdir()}, set(UNITS) | {POLKIT_RULE})


if __name__ == '__main__':
    unittest.main()
