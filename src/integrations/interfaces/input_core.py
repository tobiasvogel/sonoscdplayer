"""Pure, hardware-independent GPIO event engine. No Sonos/device commands."""
from dataclasses import dataclass, field
import re
from pathlib import Path
import tomllib

BUTTON_NAMES = ('power', 'connect', 'eject', 'previous', 'stop', 'playpause',
                'skip', 'volume_up', 'volume_dn', 'speaker_select')
PULLS = {'off', 'up', 'down'}
LEVELS = {'high', 'low'}
TRANSITIONS = {
    (0, 1): 1, (1, 3): 1, (3, 2): 1, (2, 0): 1,
    (1, 0): -1, (3, 1): -1, (2, 3): -1, (0, 2): -1,
}

@dataclass(frozen=True)
class ButtonConfig:
    pin: int | str
    pull: str
    active_level: str
    debounce_ms: int

@dataclass(frozen=True)
class EncoderConfig:
    a: int
    b: int
    switch: ButtonConfig
    pull: str
    steps_per_detent: int
    invert_direction: bool

@dataclass(frozen=True)
class ShiftRegisterConfig:
    load_pin: int
    clock_pin: int
    data_pin: int
    chips: int


@dataclass(frozen=True)
class Config:
    buttons: dict
    encoder: EncoderConfig
    poll_interval_ms: int
    power_hold_ms: int = 3000
    shift_registers: dict = field(default_factory=dict)


def _level(value):
    if value not in LEVELS:
        raise ValueError(f'Invalid active_level: {value!r}')
    return value


def _pull(value):
    if value not in PULLS:
        raise ValueError(f'Invalid pull: {value!r}')
    return value


def _ms(value, key, max_value=250):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= max_value:
        raise ValueError(f'{key} must be an integer between 0 and {max_value}')
    return value


def _pin(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 27:
        raise ValueError(f'Invalid BCM pin: {value!r}')
    return value


def _button_source(value, registers):
    if isinstance(value, str) and ':' in value:
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_]*):([0-9]+)', value)
        if match is None:
            raise ValueError(f'Invalid shift register channel: {value!r}')
        name, index_text = match.groups()
        if name not in registers or int(index_text) >= registers[name].chips * 8:
            raise ValueError(f'Unknown or out-of-range shift register channel: {value!r}')
        if index_text != str(int(index_text)):
            raise ValueError(f'Noncanonical shift register channel: {value!r}')
        return value
    return _pin(value)


REGISTER_KEYS = {'type', 'load_pin', 'clock_pin', 'data_pin', 'chips'}


def _registers(raw):
    if not isinstance(raw, dict):
        raise ValueError('shift_registers must be a table')
    registers = {}
    for name, values in raw.items():
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name):
            raise ValueError(f'Invalid shift register identifier: {name!r}')
        if not isinstance(values, dict) or values.get('type') != '74hc165':
            raise ValueError(f'{name}: type must be 74hc165')
        unknown = set(values) - REGISTER_KEYS
        if unknown:
            raise ValueError(f'{name}: unknown setting(s): {sorted(unknown)}')
        for key in ('load_pin', 'clock_pin', 'data_pin'):
            if key not in values:
                raise ValueError(f'{name}: missing {key}')
        chips = values.get('chips', 1)
        if type(chips) is not int or not 1 <= chips <= 8:
            raise ValueError(f'{name}: chips must be between 1 and 8')
        registers[name] = ShiftRegisterConfig(
            _pin(values['load_pin']), _pin(values['clock_pin']),
            _pin(values['data_pin']), chips)
    return registers


def load_config(path):
    with open(path, 'rb') as f:
        raw = tomllib.load(f)
    gpio = raw.get('gpio', {})
    if gpio.get('numbering', 'BCM') != 'BCM':
        raise ValueError('Only BCM numbering is supported')
    poll = _ms(gpio.get('poll_interval_ms', 2), 'poll_interval_ms', 1000)
    if poll == 0:
        raise ValueError('poll_interval_ms must be >=1')
    defaults = raw.get('input_defaults', {})
    overrides = raw.get('button_overrides', {})
    unknown = set(overrides) - set(BUTTON_NAMES)
    if unknown:
        raise ValueError(f'Unknown button override: {sorted(unknown)}')
    registers = _registers(raw.get('shift_registers', {}))
    b = raw['buttons']
    buttons = {}
    pins = []
    for name in BUTTON_NAMES:
        if name == 'speaker_select':
            raw_pin = b.get(name, -1)
            # TOML has no null literal. Missing, -1 and an empty quoted value
            # mean that no physical speaker button is installed.
            if raw_pin == -1 or raw_pin == '' or raw_pin == 'null':
                if name in overrides:
                    raise ValueError('Cannot override an unassigned speaker_select')
                continue
        values = defaults | overrides.get(name, {})
        source = _button_source(raw_pin if name == 'speaker_select' else b[name], registers)
        pull = _pull(values.get('pull', 'off'))
        if isinstance(source, str) and pull != 'off':
            raise ValueError(f'{name}: pull applies only to direct GPIO buttons')
        button = ButtonConfig(source, pull, _level(values.get('active_level', 'high')),
                              _ms(values.get('debounce_ms', 0), 'debounce_ms'))
        buttons[name] = button
        pins.append(source)
    e = raw['encoder']
    ec = raw.get('encoder_input', {})
    sw = ButtonConfig(_pin(e['encoder_sw']), _pull(ec.get('pull', 'off')),
                      _level(ec.get('active_level', 'low')),
                      _ms(ec.get('button_debounce_ms', 0), 'button_debounce_ms'))
    steps = ec.get('steps_per_detent', 4)
    if isinstance(steps, bool) or steps not in (1, 2, 4):
        raise ValueError('steps_per_detent must be 1, 2, or 4')
    invert = ec.get('invert_direction', False)
    if not isinstance(invert, bool):
        raise ValueError('invert_direction must be bool')
    enc = EncoderConfig(_pin(e['encoder_a']), _pin(e['encoder_b']), sw,
                        _pull(ec.get('pull', 'off')), steps, invert)
    pins += [enc.a, enc.b, enc.switch.pin]
    for reg in registers.values():
        pins.extend((reg.load_pin, reg.clock_pin, reg.data_pin))
    if len(pins) != len(set(pins)):
        raise ValueError('Duplicate GPIO pin assignment')
    hold = raw.get('power', {}).get('long_press_ms', 3000)
    if isinstance(hold, bool) or not isinstance(hold, int) or not 3000 <= hold <= 10000:
        raise ValueError('power.long_press_ms must be an integer between 3000 and 10000')
    return Config(buttons, enc, poll, hold, registers)


class Button:
    def __init__(self, config, initial, now_ms):
        self.cfg = config
        self.stable = self._active(initial)
        self.candidate = self.stable
        self.changed_at = now_ms

    def _active(self, raw):
        return bool(raw) == (self.cfg.active_level == 'high')

    def update(self, raw, now_ms):
        active = self._active(raw)
        if active != self.candidate:
            self.candidate = active
            self.changed_at = now_ms
        if active == self.stable or now_ms - self.changed_at < self.cfg.debounce_ms:
            return None
        self.stable = active
        return 'pressed' if active else 'released'


class InputEngine:
    """Call sample({bcm_pin: level}, monotonic_ms). Returns [(name, event), ...]."""
    def __init__(self, config, levels, now_ms=0):
        self.cfg = config
        self.buttons = {name: Button(b, levels[b.pin], now_ms)
                        for name, b in config.buttons.items()}
        self.buttons['encoder_sw'] = Button(config.encoder.switch,
                                            levels[config.encoder.switch.pin], now_ms)
        self.enc_last = (int(bool(levels[config.encoder.a])) << 1) | int(bool(levels[config.encoder.b]))
        self.enc_acc = 0

    def sample(self, levels, now_ms):
        events = []
        for name, button in self.buttons.items():
            ev = button.update(levels[button.cfg.pin], now_ms)
            if ev:
                events.append((name, ev))
        e = self.cfg.encoder
        new = (int(bool(levels[e.a])) << 1) | int(bool(levels[e.b]))
        if new != self.enc_last:
            delta = TRANSITIONS.get((self.enc_last, new), 0)
            if not delta:
                self.enc_acc = 0 # illegal two-bit jump: ignore, resynchronize
            else:
                self.enc_acc += -delta if e.invert_direction else delta
                if abs(self.enc_acc) >= e.steps_per_detent:
                    events.append(('encoder', 'right' if self.enc_acc > 0 else 'left'))
                    self.enc_acc = 0
            self.enc_last = new
        return events
