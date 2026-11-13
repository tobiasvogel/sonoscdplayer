"""Offline checks for the one-time post-merge systemd unit preparation."""
from pathlib import Path
import tempfile
import unittest

from tools.render_runtime_units import UNITS, render, main


ROOT = Path(__file__).resolve().parents[1]


class RuntimeUnitRenderTests(unittest.TestCase):
    def setUp(self):
        self.inputs = dict(
            checkout='/opt/example/src',
            player_config='/etc/example/player.toml',
            interface_config='/etc/example/interface.toml',
            ui_config='/etc/example/ui.toml',
            python_venv='/opt/example/.venv/bin/python',
            user='example',
        )

    def test_renders_four_complete_units_and_preserves_safety_controls(self):
        units = render(ROOT / 'systemd', **self.inputs)
        self.assertEqual(set(units), set(UNITS))
        for name, content in units.items():
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
        self.assertNotIn('--adopt-boot-splash', oled)
        buttons = units['sonoscdplayer-buttons.service']
        self.assertIn('After=local-fs.target sonoscdplayer-oled.service', buttons)
        self.assertIn('--execute --selected-target-execute', buttons)
        self.assertNotIn('[Install]', buttons)
        self.assertNotIn('--power-actions', buttons)
        self.assertNotIn('--ui-power-actions', oled)
        self.assertNotIn('50-sonoscdplayer-power.rules', units)

    def test_power_actions_enable_both_switches_and_render_polkit_rule(self):
        units = render(ROOT / 'systemd', power_actions=True, **self.inputs)
        self.assertIn('--execute --selected-target-execute --power-actions',
                      units['sonoscdplayer-buttons.service'])
        self.assertIn('--ui-target-rpc --ui-power-actions',
                      units['sonoscdplayer-oled.service'])
        rule = units['50-sonoscdplayer-power.rules']
        self.assertIn('subject.user === "example"', rule)
        self.assertNotIn('@SONOSCDPLAYER_', rule)

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
                ('--player-config', self.inputs['player_config']),
                ('--interface-config', self.inputs['interface_config']),
                ('--ui-config', self.inputs['ui_config']),
                ('--python-venv', self.inputs['python_venv']),
                ('--service-user', self.inputs['user']),
            ):
                argv.extend((name, value))
            main(argv)
            self.assertEqual({p.name for p in out.iterdir()}, set(UNITS))


if __name__ == '__main__':
    unittest.main()
