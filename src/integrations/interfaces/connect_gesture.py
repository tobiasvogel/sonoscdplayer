"""Pure CONNECT gesture interpretation; no GPIO, network or Sonos operations."""

MENU_MS = 3000
WARNING_MS = 5000
SETUP_MS = 10000


class ConnectGesture:
    """Emit semantic CONNECT actions while held and on release.

    While held: a one-time ``settings_hint`` after three seconds (releasing now
    opens the network settings), a countdown ``warning_5`` .. ``warning_1``
    from five seconds, and ``setup_requested`` as soon as ten seconds are
    reached, without waiting for the release. A release after that emits
    nothing more. Shorter releases emit ``status`` or ``settings``.

    All time values are caller-supplied monotonic milliseconds. Requesting
    setup mode never configures the network.
    """

    def __init__(self):
        self._pressed_at = None
        self._last_countdown = None
        self._hinted = False
        self._setup_sent = False

    @property
    def pressed(self):
        return self._pressed_at is not None

    def press(self, now_ms):
        if self.pressed:
            return None
        self._pressed_at = now_ms
        self._last_countdown = None
        self._hinted = False
        self._setup_sent = False
        return None

    def tick(self, now_ms):
        if not self.pressed or now_ms < self._pressed_at:
            return None
        elapsed = now_ms - self._pressed_at
        if self._setup_sent:
            return None
        if elapsed >= SETUP_MS:
            self._setup_sent = True
            return 'setup_requested'
        if elapsed < MENU_MS:
            return None
        if elapsed < WARNING_MS:
            if self._hinted:
                return None
            self._hinted = True
            return 'settings_hint'
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
        setup_sent = self._setup_sent
        self._pressed_at = None
        self._last_countdown = None
        self._hinted = False
        self._setup_sent = False
        if setup_sent or now_ms < started:
            return None
        elapsed = now_ms - started
        if elapsed >= SETUP_MS:
            return 'setup_requested'
        if elapsed >= MENU_MS:
            return 'settings'
        return 'status'
