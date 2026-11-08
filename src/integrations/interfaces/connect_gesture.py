"""Pure CONNECT gesture interpretation; no GPIO, network or Sonos operations."""

MENU_MS = 3000
WARNING_MS = 5000
SETUP_MS = 10000


class ConnectGesture:
    """Emit semantic actions on release and a countdown while held.

    All time values are caller-supplied monotonic milliseconds. Holding ten
    seconds merely requests setup mode; it never configures the network.
    """

    def __init__(self):
        self._pressed_at = None
        self._last_countdown = None

    @property
    def pressed(self):
        return self._pressed_at is not None

    def press(self, now_ms):
        if self.pressed:
            return None
        self._pressed_at = now_ms
        self._last_countdown = None
        return None

    def tick(self, now_ms):
        if not self.pressed or now_ms < self._pressed_at:
            return None
        elapsed = now_ms - self._pressed_at
        if elapsed < WARNING_MS:
            return None
        # Fixed five-second warning window, 5..1. No reset is performed.
        remaining = max(1, (SETUP_MS - elapsed + 999) // 1000)
        if remaining == self._last_countdown:
            return None
        self._last_countdown = remaining
        return f'warning_{remaining}'

    def release(self, now_ms):
        if not self.pressed:
            return None
        started = self._pressed_at
        self._pressed_at = None
        self._last_countdown = None
        if now_ms < started:
            return None
        elapsed = now_ms - started
        if elapsed >= SETUP_MS:
            return 'setup_requested'
        if elapsed >= MENU_MS:
            return 'settings'
        return 'status'
