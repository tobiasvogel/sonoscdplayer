"""Encoder direction configuration and UI event mapping regression tests.

No Raspberry Pi, GPIO library, Sonos, or display required.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from integrations.interfaces.input_core import InputEngine, load_config
from sonos_ui.input_adapter import to_ui_action

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'src/integrations/interfaces/interface.toml'


def direction_events(cfg):
    levels = {button.pin: 0 for button in cfg.buttons.values()}
    levels[cfg.encoder.switch.pin] = 0
    levels[cfg.encoder.a] = 0
    levels[cfg.encoder.b] = 0
    engine = InputEngine(cfg, levels, 0)
    events = []
    # One full quadrature cycle: 00 -> 01 -> 11 -> 10 -> 00.
    for ms, (a, b) in enumerate(((0, 1), (1, 1), (1, 0), (0, 0)), 1):
        levels[cfg.encoder.a] = a
        levels[cfg.encoder.b] = b
        events.extend(engine.sample(levels, ms))
    return [event for event in events if event[0] == 'encoder']


class EncoderDirectionConfigTests(unittest.TestCase):
    def load_with_direction(self, setting):
        template = CONFIG_PATH.read_text(encoding='utf-8')
        self.assertIn('invert_direction = false', template)
        new_text = template.replace('invert_direction = false', setting)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'interface.toml'
            path.write_text(new_text, encoding='utf-8')
            return load_config(path)

    def test_default_direction_is_backward_compatible(self):
        cfg = load_config(CONFIG_PATH)
        self.assertFalse(cfg.encoder.invert_direction)
        self.assertEqual(direction_events(cfg), [('encoder', 'right')])

    def test_reversing_direction_flips_one_detent(self):
        cfg = self.load_with_direction('invert_direction = true')
        self.assertTrue(cfg.encoder.invert_direction)
        self.assertEqual(direction_events(cfg), [('encoder', 'left')])

    def test_ui_navigation_uses_logical_direction_without_second_inversion(self):
        normal = direction_events(load_config(CONFIG_PATH))[0]
        reverse = direction_events(self.load_with_direction('invert_direction = true'))[0]
        self.assertEqual(to_ui_action(*normal), 'navigate_next')
        self.assertEqual(to_ui_action(*reverse), 'navigate_previous')

    def test_non_boolean_direction_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invert_direction must be bool'):
            self.load_with_direction('invert_direction = "true"')

    def test_missing_setting_defaults_to_false(self):
        cfg = self.load_with_direction('# invert_direction omitted')
        self.assertFalse(cfg.encoder.invert_direction)
        self.assertEqual(direction_events(cfg), [('encoder', 'right')])


if __name__ == '__main__':
    unittest.main()
