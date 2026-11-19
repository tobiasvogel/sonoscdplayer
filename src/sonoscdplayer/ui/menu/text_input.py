"""Character-by-character text entry with the rotary encoder.

Pure state, no GPIO, network or display access, so any menu can reuse it:
turning the encoder moves a character wheel, pressing takes the shown
character or runs the shown action (done, delete last character, cancel).
Rendering lives in ``ui/renderers/oled_128_text_input.py``.
"""
from __future__ import annotations

import string

DONE = '\x00done'
DELETE = '\x00delete'
CANCEL = '\x00cancel'
ACTIONS = (DONE, DELETE, CANCEL)

#: Printable ASCII in reading order (what a WPA2 passphrase may contain).
ASCII_CHARACTERS = (string.ascii_lowercase + string.ascii_uppercase + string.digits
                    + ' ' + string.punctuation)
#: Default additions for names (not passwords), configurable in ui.toml.
DEFAULT_EXTRA_CHARACTERS = 'äöüÄÖÜß'
MAX_EXTRA_CHARACTERS = 64

#: Encoder steps closer together than this count as fast turning.
FAST_TURN_SECONDS = 0.08
FAST_TURN_STEP = 4


def clean_extra_characters(value):
    """Validate configured extra characters; ASCII duplicates are dropped."""
    if not isinstance(value, str):
        raise ValueError('extra characters must be a string')
    result = ''
    for char in value:
        if not char.isprintable() or char.isspace():
            raise ValueError('extra characters must be printable and not blank')
        if char not in ASCII_CHARACTERS and char not in result:
            result += char
    if len(result) > MAX_EXTRA_CHARACTERS:
        raise ValueError(f'at most {MAX_EXTRA_CHARACTERS} extra characters')
    return result


def wheel_items(extra=''):
    """Letters, extras, digits, space, punctuation, then the three actions.

    The wheel wraps around: from the start ('a') one step left is DONE,
    then DELETE, then CANCEL.
    """
    letters = string.ascii_lowercase + string.ascii_uppercase
    rest = string.digits + ' ' + string.punctuation
    extras = clean_extra_characters(extra)
    return tuple(letters + extras + rest) + (CANCEL, DELETE, DONE)


class TextEntry:
    """One text being entered.

    ``max_bytes`` counts UTF-8 bytes (a WLAN name has at most 32 bytes,
    an umlaut uses two). ``validate(text)`` returns '' or an error locale
    key with optional params as ``(key, params)``.
    """

    def __init__(self, *, extra_characters='', max_bytes=63, text='',
                 validate=None):
        self.items = wheel_items(extra_characters)
        self.max_bytes = max_bytes
        self.text = text
        self.position = 0
        self.result = None          # None while editing, then 'done' or 'cancelled'
        self.error = None           # (locale key, params) shown until the next input
        self._validate = validate
        self._last_turn = None

    # ---- read side -------------------------------------------------------
    @property
    def current(self):
        return self.items[self.position]

    def neighbour(self, offset):
        return self.items[(self.position + offset) % len(self.items)]

    @property
    def byte_length(self):
        return len(self.text.encode('utf-8'))

    # ---- input -----------------------------------------------------------
    def turn(self, step, *, now=None):
        """Move the wheel; fast turning (encoder detents < 80 ms apart) jumps."""
        if now is not None and self._last_turn is not None and \
                0 <= now - self._last_turn[0] < FAST_TURN_SECONDS and \
                (self._last_turn[1] > 0) == (step > 0):
            distance = step * FAST_TURN_STEP
        else:
            distance = step
        if now is not None:
            self._last_turn = (now, step)
        self.position = (self.position + distance) % len(self.items)
        self.error = None
        return True

    def finish(self):
        """Same as pressing on DONE (e.g. a short CONNECT press as shortcut)."""
        return self._run(DONE)

    def press(self):
        """Take the shown character or run the shown action."""
        return self._run(self.current)

    def _run(self, item):
        self.error = None
        if item == CANCEL:
            self.result = 'cancelled'
        elif item == DELETE:
            self.text = self.text[:-1]
        elif item == DONE:
            problem = self._validate(self.text) if self._validate else None
            if problem:
                self.error = problem if isinstance(problem, tuple) else (problem, {})
            else:
                self.result = 'done'
        elif len((self.text + item).encode('utf-8')) > self.max_bytes:
            self.error = ('textinput.too_long', {'count': self.max_bytes})
        else:
            self.text += item
        return True

    def reopen(self, error=None):
        """Edit again (e.g. after a failed connection), keeping the text."""
        self.result = None
        self.error = error
        self.position = 0
        self._last_turn = None
