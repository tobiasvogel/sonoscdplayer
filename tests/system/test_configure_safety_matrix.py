"""M4b.16: cross-file wizard safety contract, entirely offline.

No GPIO, SPI, systemctl or player socket is contacted. Service commands are
replaced by an in-memory subprocess stub in the rollback regression test.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def load_tool(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wizard = load_tool('configure.py', 'm4b16_wizard')
inputs = load_tool('configure_inputs.py', 'm4b16_inputs')
swap = load_tool('config_swap.py', 'm4b16_swap')


class ConfigSafetyMatrix(unittest.TestCase):
    def setUp(self):
        self.player_raw = (ROOT / 'config/player.example.toml').read_text(encoding='utf-8')
        self.ui_raw = (ROOT / 'config/ui.example.toml').read_text(encoding='utf-8')
        self.interface_raw = (ROOT / 'config/interface.example.toml').read_text(encoding='utf-8')
        self.player_current = wizard.existing_values(
            tomllib.loads(self.player_raw), tomllib.loads(self.player_raw))
        self.ui_current = wizard.existing_ui_values(
            tomllib.loads(self.ui_raw), tomllib.loads(self.ui_raw))

    def plan(self, changes):
        return wizard.plan_update(
            changes, self.player_current, self.ui_current,
            player_path=Path('/tmp/player.toml'), ui_path=Path('/tmp/ui.toml'),
            raw=self.player_raw, ui_raw=self.ui_raw)

    def test_player_only_selects_player_service(self):
        path, raw, _, services = self.plan({('sonos', 'use_queue'): False})
        self.assertEqual(path.name, 'player.toml')
        self.assertEqual(services, ['sonoscdplayer-player.service'])
        self.assertFalse(tomllib.loads(raw)['sonos']['use_queue'])

    def test_ui_only_selects_ui_consumers(self):
        changed = 'en' if self.ui_current[('ui', 'language')] == 'de' else 'de'
        path, raw, _, services = self.plan({('ui', 'language'): changed})
        self.assertEqual(path.name, 'ui.toml')
        self.assertEqual(services, list(wizard.UI_SERVICES))
        self.assertEqual(tomllib.loads(raw)['ui']['language'], changed)

    def test_led_only_does_not_restart_player_or_buttons(self):
        _, raw, _, services = self.plan({('led_outputs', 'brightness'): 0.31})
        self.assertEqual(services, ['sonoscdplayer-led.service'])
        self.assertEqual(tomllib.loads(raw)['led_outputs']['brightness'], 0.31)

    def test_player_and_led_share_one_player_file(self):
        _, _, _, services = self.plan({('led_outputs', 'brightness'): 0.31,
                                       ('sonos', 'use_queue'): False})
        self.assertEqual(services, ['sonoscdplayer-player.service',
                                    'sonoscdplayer-led.service'])

    def test_mixed_player_and_ui_is_rejected_before_write(self):
        with self.assertRaisesRegex(ValueError, 'span player.toml and ui.toml'):
            self.plan({('sonos', 'use_queue'): False, ('ui', 'language'): 'en'})

    def test_unknown_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown configuration change'):
            self.plan({('display', 'controller'): 'st7735s'})

    def test_led_runtime_rejects_invalid_combination(self):
        with self.assertRaises(ValueError):
            self.plan({('led_outputs', 'brightness'): 1.5})

    def test_metadata_conflicts_are_rejected_before_write(self):
        with self.assertRaises(ValueError):
            self.plan({('metadata.matching', 'soft_suffixes'): ['Live']})

    def test_input_preview_preserves_bytes_and_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'interface.toml'
            original = ('# personal note\n' + self.interface_raw).encode('utf-8')
            path.write_bytes(original)
            candidate = inputs.prepare(path.read_text(encoding='utf-8'),
                                       {'encoder_input.invert_direction': True})
            inputs.validate(candidate)
            self.assertEqual(path.read_bytes(), original)
            self.assertIn('# personal note', candidate)

    def test_input_gpio_collision_is_rejected_without_hardware(self):
        raw = self.interface_raw.replace('encoder_b = 26', 'encoder_b = 6')
        with self.assertRaisesRegex(ValueError, 'Duplicate GPIO pin'):
            inputs.validate(raw)

    def test_input_unsupported_pin_edit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsupported interface option'):
            inputs.prepare(self.interface_raw, {'buttons.power': 4})

    def test_input_invalid_debounce_is_rejected(self):
        with self.assertRaises(ValueError):
            inputs.parse_value('input_defaults.debounce_ms', '251')

    def test_missing_interface_does_not_create_it(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / 'interface.toml'
            output = []
            self.assertIsNone(inputs.interactive_preview(missing, out=output.append))
            self.assertFalse(missing.exists())

    def test_config_swap_rolls_back_when_new_service_is_inactive(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / 'player.toml'
            proposed = Path(temp) / 'new.toml'
            original_bytes = b'[streaming]\nmax_buffer_mib = 16\n'
            original.write_bytes(original_bytes)
            proposed.write_bytes(b'[streaming]\nmax_buffer_mib = 32\n')
            commands = []

            def fake_run(argv, **kwargs):
                commands.append(tuple(argv))
                if argv[:2] == ['systemctl', 'is-active']:
                    return subprocess.CompletedProcess(argv, 1)
                return subprocess.CompletedProcess(argv, 0)

            def idle_status():
                return {'player': {'mode': 'NO_DISC'},
                        'sonos': {'owned': False, 'group_cleanup_pending': False},
                        'stream': {'active': False}}

            with self.assertRaises(swap.SwapError):
                swap.swap(original, proposed, services=['sonoscdplayer-player.service'],
                          apply=True, run=fake_run, read_state=idle_status,
                          sleep=lambda _: None, out=lambda _: None)
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertIn(('systemctl', 'reset-failed', 'sonoscdplayer-player.service'), commands)
            self.assertTrue(any(p.name.startswith('player.toml.bak-')
                                for p in Path(temp).iterdir()))

    def test_config_swap_preview_never_calls_systemctl(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / 'player.toml'
            proposed = Path(temp) / 'new.toml'
            original.write_text('[streaming]\nmax_buffer_mib = 16\n')
            proposed.write_text('[streaming]\nmax_buffer_mib = 32\n')
            commands = []

            def fake_run(argv, **kwargs):
                commands.append(tuple(argv))
                return subprocess.CompletedProcess(argv, 0)

            rc = swap.swap(original, proposed,
                           services=['sonoscdplayer-player.service'],
                           apply=False, run=fake_run, read_state=lambda: None,
                           out=lambda _: None)
            self.assertEqual(rc, 0)
            self.assertEqual(commands, [])
            self.assertEqual(tomllib.loads(original.read_text())['streaming']['max_buffer_mib'], 16)


if __name__ == '__main__':
    unittest.main()
