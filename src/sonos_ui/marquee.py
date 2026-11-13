"""Pure pixel-based marquee; initial/end pauses and reset are explicit."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Marquee:
    initial_pause: float = 1.5
    end_pause: float = 0.8
    pixels_per_second: float = 18.0

    def offset(self, text_width: int, viewport_width: int, elapsed: float) -> int:
        overflow = max(0, text_width - viewport_width)
        if overflow == 0 or elapsed <= self.initial_pause:
            return 0
        travel = overflow / self.pixels_per_second
        period = self.initial_pause + travel + self.end_pause
        phase = elapsed % period
        if phase <= self.initial_pause:
            return 0
        if phase <= self.initial_pause + travel:
            return min(overflow, int((phase - self.initial_pause) * self.pixels_per_second))
        return overflow


class MarqueeClock:
    """Shared clock that animates every scrolling text without caller timing.

    Each text is identified by its position, viewport and content. A text
    starts with the initial pause when it first appears and restarts after it
    changed or disappeared for longer than ``forget_after`` (D-197). The
    display loop asks ``animating()`` to refresh faster only while some
    visible text actually overflows its viewport.
    """

    def __init__(self, *, forget_after=1.0, clock=None):
        import time as _time
        self._clock = clock or _time.monotonic
        self._forget_after = forget_after
        self._started = {}
        self._last_overflow = float('-inf')

    def elapsed(self, key, *, overflows):
        now = self._clock()
        started, last_seen = self._started.get(key, (now, now))
        if now - last_seen > self._forget_after:
            started = now
        self._started[key] = (started, now)
        if overflows:
            self._last_overflow = now
        if len(self._started) > 256:
            cutoff = now - self._forget_after
            self._started = {k: v for k, v in self._started.items() if v[1] >= cutoff}
        return now - started

    def animating(self, within=1.0):
        return self._clock() - self._last_overflow <= within


# Process-wide clock: renderers are often recreated for every frame.
MARQUEE_CLOCK = MarqueeClock()
