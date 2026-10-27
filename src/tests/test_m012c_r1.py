import tempfile
import unittest
from pathlib import Path
from src.integrations.interfaces.input_core import load_config, InputEngine, Button

PATH = Path(__file__).resolve().parents[1] / 'src/integrations/interfaces/interface.toml'

class InputTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(PATH)
        self.levels = {b.pin: 0 for b in self.cfg.buttons.values()}
        e = self.cfg.encoder
        self.levels.update({e.a: 0, e.b: 0, e.switch.pin: 1})
        self.engine = InputEngine(self.cfg, self.levels)

    def test_pins(self):
        self.assertEqual(self.cfg.buttons['power'].pin, 17)
        self.assertEqual(self.cfg.encoder.a, 6)
        self.assertEqual(self.cfg.buttons['stop'].pull, 'off')

    def test_active_high_press_and_release(self):
        self.levels[18] = 1
        self.assertIn(('stop', 'pressed'), self.engine.sample(self.levels, 10))
        self.assertEqual(self.engine.sample(self.levels, 11), [])
        self.levels[18] = 0
        self.assertIn(('stop', 'released'), self.engine.sample(self.levels, 12))

    def test_encoder_button_active_low(self):
        self.levels[5] = 0
        self.assertIn(('encoder_sw', 'pressed'), self.engine.sample(self.levels, 1))

    def test_debounce(self):
        from src.integrations.interfaces.input_core import ButtonConfig
        b = Button(ButtonConfig(17, 'up', 'low', 25), initial=1, now_ms=0)
        self.assertIsNone(b.update(0, 10))
        self.assertIsNone(b.update(1, 12))
        self.assertIsNone(b.update(0, 14))
        self.assertIsNone(b.update(0, 38))
        self.assertEqual(b.update(0, 39), 'pressed')
        self.assertIsNone(b.update(0, 100))

    def test_encoder_right(self):
        # States: 00 -> 01 -> 11 -> 10 -> 00.
        e = self.cfg.encoder
        got = []
        for t, state in enumerate([1, 3, 2, 0], 1):
            self.levels[e.a] = state >> 1
            self.levels[e.b] = state & 1
            got.extend(self.engine.sample(self.levels, t))
        self.assertEqual(got, [('encoder', 'right')])

    def test_encoder_left(self):
        e = self.cfg.encoder
        got = []
        for t, state in enumerate([2, 3, 1, 0], 1):
            self.levels[e.a] = state >> 1
            self.levels[e.b] = state & 1
            got.extend(self.engine.sample(self.levels, t))
        self.assertEqual(got, [('encoder', 'left')])

    def test_invalid_encoder_jump(self):
        e = self.cfg.encoder
        self.levels[e.a] = self.levels[e.b] = 1
        self.assertEqual(self.engine.sample(self.levels, 1), [])
        self.levels[e.a] = self.levels[e.b] = 0
        self.assertEqual(self.engine.sample(self.levels, 2), [])

    def test_duplicate_gpio_rejected(self):
        text = PATH.read_text().replace('stop = 18', 'stop = 17')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interface.toml'
            p.write_text(text)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                load_config(p)

    def test_per_button_override(self):
        text = PATH.read_text() + '\n[button_overrides.stop]\npull="up"\nactive_level="low"\ndebounce_ms=30\n'
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interface.toml'
            p.write_text(text)
            c = load_config(p)
            self.assertEqual(c.buttons['stop'].pull, 'up')
            self.assertEqual(c.buttons['stop'].debounce_ms, 30)
            self.assertEqual(c.buttons['previous'].pull, 'off')

    def test_invalid_config(self):
        text = PATH.read_text().replace('steps_per_detent = 4', 'steps_per_detent = 3')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interface.toml'
            p.write_text(text)
            with self.assertRaisesRegex(ValueError, 'steps_per_detent'):
                load_config(p)

if __name__ == '__main__':
    unittest.main()
