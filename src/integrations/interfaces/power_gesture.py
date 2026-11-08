"""Pure short/long press detection for the power button. No GPIO, no commands."""
from __future__ import annotations

MIN_HOLD_MS = 3000
MAX_HOLD_MS = 10000


class PowerGesture:
    """Feed debounced ``pressed``/``released`` events and a clock.

    ``release`` returns ``'short'`` when the button was let go before ``hold_ms``;
    ``tick`` returns ``'long'`` exactly once while the button is still held. A
    long press never produces a short one afterwards. A release without a seen
    press (button already down at start-up) is ignored.
    """

    def __init__(self, hold_ms=MIN_HOLD_MS):
        if isinstance(hold_ms, bool) or not isinstance(hold_ms, int) or not MIN_HOLD_MS <= hold_ms <= MAX_HOLD_MS:
            raise ValueError(f'hold_ms must be an integer between {MIN_HOLD_MS} and {MAX_HOLD_MS}')
        self.hold_ms = hold_ms
        self._pressed_at = None
        self._long_sent = False

    def press(self, now_ms):
        self._pressed_at = now_ms
        self._long_sent = False

    def tick(self, now_ms):
        if self._pressed_at is None or self._long_sent:
            return None
        if now_ms - self._pressed_at >= self.hold_ms:
            self._long_sent = True
            return 'long'
        return None

    def release(self, now_ms):
        if self._pressed_at is None:
            return None
        long_sent = self._long_sent
        self._pressed_at = None
        self._long_sent = False
        return None if long_sent else 'short'


class PowerCoordinator:
    """Glue between button events and the two power paths.

    short press -> ``publish('power', 'short')`` so the UI process opens its menu
    long press  -> ``shutdown()`` directly, independent of the UI process

    Both callables must not block; ``shutdown`` is expected to hand the work to a
    thread. Only the ``power`` button is looked at.
    """

    def __init__(self, hold_ms, *, publish, shutdown):
        self.gesture = PowerGesture(hold_ms)
        self._publish = publish
        self._shutdown = shutdown

    def event(self, name, event, now_ms):
        if name != 'power':
            return
        if event == 'pressed':
            self.gesture.press(now_ms)
        elif event == 'released' and self.gesture.release(now_ms) == 'short':
            self._publish('power', 'short')

    def tick(self, now_ms):
        if self.gesture.tick(now_ms) == 'long':
            self._shutdown()
