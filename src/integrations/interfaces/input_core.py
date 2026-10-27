"""Pure, hardware-independent GPIO event engine. No Sonos/device commands."""
from dataclasses import dataclass
from pathlib import Path
import tomllib

BUTTON_NAMES = ('power', 'connect', 'eject', 'previous', 'stop', 'playpause',
                'skip', 'volume_up', 'volume_dn')
PULLS = {'off', 'up', 'down'}
LEVELS = {'high', 'low'}
TRANSITIONS = {
    (0, 1): 1, (1, 3): 1, (3, 2): 1, (2, 0): 1,
    (1, 0): -1, (3, 1): -1, (2, 3): -1, (0, 2): -1,
}

@dataclass(frozen=True)
class ButtonConfig:
    pin: int
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
class Config:
    buttons: dict
    encoder: EncoderConfig
    poll_interval_ms: int


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
    b = raw['buttons']
    buttons = {}
    pins = []
    for name in BUTTON_NAMES:
        values = defaults | overrides.get(name, {})
        button = ButtonConfig(_pin(b[name]), _pull(values.get('pull', 'off')),
                              _level(values.get('active_level', 'high')),
                              _ms(values.get('debounce_ms', 0), 'debounce_ms'))
        buttons[name] = button
        pins.append(button.pin)
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
    if len(pins) != len(set(pins)):
        raise ValueError('Duplicate GPIO pin assignment')
    return Config(buttons, enc, poll)


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
