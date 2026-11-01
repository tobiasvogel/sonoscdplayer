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
