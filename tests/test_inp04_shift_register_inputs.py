"""INP-04: mixed direct GPIO and 74HC165 button sources."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from integrations.interfaces.input_core import InputEngine, load_config
from integrations.interfaces.interface_input import input_sources, pins_for
from integrations.interfaces.shift_register_74hc165 import ShiftRegisterReader


EXAMPLE = Path(__file__).resolve().parents[1] / 'config' / 'interface.example.toml'


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
