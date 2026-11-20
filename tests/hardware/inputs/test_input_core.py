"""Input engine: button/encoder configuration, debounce, encoder direction and 74HC165 shift-register sources (no GPIO)."""
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
ROOT = Path(__file__).resolve().parents[3]
from sonoscdplayer.hardware.inputs.event_engine import load_config, InputEngine, Button
from sonoscdplayer.hardware.inputs.button_service import input_sources, pins_for
from sonoscdplayer.hardware.inputs.shift_register_74hc165 import ShiftRegisterReader
PATH = Path(__file__).resolve().parents[3] / 'config/interface.example.toml'
CONFIG_PATH = ROOT / 'config/interface.example.toml'
EXAMPLE = Path(__file__).resolve().parents[3] / 'config' / 'interface.example.toml'



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
        from sonoscdplayer.hardware.inputs.event_engine import ButtonConfig
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

    def test_example_configuration_places_direction_in_encoder_input(self):
        import tomllib
        example_path = ROOT / 'config/interface.example.toml'
        with example_path.open('rb') as stream:
            example = tomllib.load(stream)
        with CONFIG_PATH.open('rb') as stream:
            installed_template = tomllib.load(stream)
        self.assertIn('invert_direction', example['encoder_input'])
        self.assertIs(example['encoder_input']['invert_direction'], False)
        self.assertEqual(example['encoder_input'], installed_template['encoder_input'])
        self.assertFalse(load_config(example_path).encoder.invert_direction)

    def test_default_direction_is_backward_compatible(self):
        cfg = load_config(CONFIG_PATH)
        self.assertFalse(cfg.encoder.invert_direction)
        self.assertEqual(direction_events(cfg), [('encoder', 'right')])

    def test_reversing_direction_flips_one_detent(self):
        cfg = self.load_with_direction('invert_direction = true')
        self.assertTrue(cfg.encoder.invert_direction)
        self.assertEqual(direction_events(cfg), [('encoder', 'left')])

    def test_non_boolean_direction_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invert_direction must be bool'):
            self.load_with_direction('invert_direction = "true"')

    def test_missing_setting_defaults_to_false(self):
        cfg = self.load_with_direction('# invert_direction omitted')
        self.assertFalse(cfg.encoder.invert_direction)
        self.assertEqual(direction_events(cfg), [('encoder', 'right')])


def config_from(text, *, register_block=''):
    original = EXAMPLE.read_text(encoding='utf-8')
    original = original.replace('stop = 18', 'stop = "sr0:2"')
    original = original.replace('playpause = 14', 'playpause = "sr0:3"')
    original = original.replace('speaker_select = -1', 'speaker_select = "sr0:7"')
    original += '\n' + register_block + '\n'
    original = original.replace('stop = "sr0:2"', text)
    with TemporaryDirectory() as folder:
        target = Path(folder) / 'interface.toml'
        target.write_text(original, encoding='utf-8')
        return load_config(target)


REGISTERS = """
[shift_registers.sr0]
type = "74hc165"
load_pin = 19
clock_pin = 23
data_pin = 4
chips = 1
"""


class ConfigTests(unittest.TestCase):
    def test_mixed_sources_and_speaker_selection(self):
        cfg = config_from('stop = "sr0:2"', register_block=REGISTERS)
        self.assertEqual(cfg.buttons['power'].pin, 17)
        self.assertEqual(cfg.buttons['stop'].pin, 'sr0:2')
        self.assertEqual(cfg.buttons['speaker_select'].pin, 'sr0:7')
        self.assertNotIn('sr0:2', pins_for(cfg))
        self.assertIn('sr0:2', input_sources(cfg))
        levels = {source: 0 for source in input_sources(cfg)}
        levels[cfg.encoder.switch.pin] = 1
        engine = InputEngine(cfg, levels, 0)
        levels['sr0:2'] = 1
        self.assertIn(('stop', 'pressed'), engine.sample(levels, 10))
        levels['sr0:2'] = 0
        self.assertIn(('stop', 'released'), engine.sample(levels, 20))

    def test_existing_gpio_config_still_parses(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / 'interface.toml'
            target.write_text(EXAMPLE.read_text(encoding='utf-8'),
                              encoding='utf-8')
            cfg = load_config(target)
        self.assertEqual(cfg.shift_registers, {})
        self.assertEqual(cfg.buttons['stop'].pin, 18)

    def test_missing_or_unknown_register_keys(self):
        for missing in ('load_pin', 'clock_pin', 'data_pin'):
            block = '\n'.join(
                line for line in REGISTERS.splitlines()
                if not line.startswith(missing + ' =')
            )
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(ValueError, f'sr0: missing {missing}'):
                    config_from('stop = "sr0:2"', register_block=block)
        with self.assertRaisesRegex(ValueError, "unknown setting.*'chip'"):
            config_from('stop = "sr0:2"', register_block=REGISTERS + 'chip = 2\n')

    def test_invalid_register_channel_and_collision(self):
        cases = [
            ('stop = "sr0:8"', REGISTERS),
            ('stop = "sr1:0"', REGISTERS),
            ('stop = "sr0:02"', REGISTERS),
            ('stop = "sr0:-1"', REGISTERS),
            ('stop = true', REGISTERS),
            ('stop = 19', REGISTERS),  # LOAD pin conflict
            ('stop = "sr0:3"', REGISTERS),  # PLAY/PAUSE conflict
            ('stop = "sr0:2"', REGISTERS.replace('data_pin = 4', 'data_pin = 19')),
            ('stop = "sr0:2"', REGISTERS.replace('chips = 1', 'chips = 0')),
        ]
        for mapping, registers in cases:
            with self.subTest(mapping=mapping, registers=registers):
                with self.assertRaises((ValueError, KeyError)):
                    config_from(mapping, register_block=registers)

    def test_two_independent_registers_and_cascade_config(self):
        registers = REGISTERS.replace('chips = 1', 'chips = 2')
        registers += """
[shift_registers.sr1]
type = "74hc165"
load_pin = 12
clock_pin = 13
data_pin = 25
chips = 1
"""
        cfg = config_from('stop = "sr0:15"', register_block=registers)
        self.assertEqual(cfg.buttons['stop'].pin, 'sr0:15')
        self.assertEqual(cfg.shift_registers['sr0'].chips, 2)
        self.assertEqual(len(cfg.shift_registers), 2)


class FakeGPIO:
    OUT = 1
    IN = 0
    HIGH = 1
    LOW = 0

    def __init__(self, bits):
        self.bits = list(bits)
        self.outputs = []
        self.configured = []

    def setup(self, pin, mode, **kwargs):
        self.configured.append((pin, mode))

    def output(self, pin, level):
        self.outputs.append((pin, level))

    def input(self, pin):
        return self.bits.pop(0)


class ReaderTests(unittest.TestCase):
    def test_single_register_bit_order(self):
        cfg = config_from('stop = "sr0:2"', register_block=REGISTERS)
        # Serial stream Q7(D7) ... Q7(D0).
        gpio = FakeGPIO([1, 1, 0, 0, 0, 1, 0, 1])
        reader = ShiftRegisterReader(gpio, cfg.shift_registers)
        reader.setup()
        result = reader.sample()
        self.assertEqual([result[f'sr0:{i}'] for i in range(8)],
                         [1, 0, 1, 0, 0, 0, 1, 1])
        self.assertEqual(gpio.bits, [])
        self.assertEqual(gpio.outputs.count((23, 1)), 7)
        self.assertIn((19, 0), gpio.outputs)
        self.assertIn((19, 1), gpio.outputs)

    def test_cascade_channels_and_multiple_registers(self):
        registers = REGISTERS.replace('chips = 1', 'chips = 2')
        registers += """
[shift_registers.sr1]
type = "74hc165"
load_pin = 12
clock_pin = 13
data_pin = 25
chips = 1
"""
        cfg = config_from('stop = "sr0:15"', register_block=registers)
        gpio = FakeGPIO([1] + [0]*7 + [0]*7 + [1] + [1]*8)
        reader = ShiftRegisterReader(gpio, cfg.shift_registers)
        reader.setup()
        values = reader.sample()
        self.assertEqual(values['sr0:7'], 1)
        self.assertEqual(values['sr0:8'], 1)
        self.assertEqual(values['sr0:0'], 0)
        self.assertEqual(values['sr1:0'], 1)
        self.assertEqual(len(values), 24)


if __name__ == '__main__':
    unittest.main()
