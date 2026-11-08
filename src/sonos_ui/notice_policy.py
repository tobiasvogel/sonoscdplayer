"""Pure display timing decisions for notices; no timers or input handlers.

UI-NOTICE-02 consumes these decisions without hardware dependencies.
"""
from dataclasses import dataclass
from math import isfinite
from .notice import Notice


@dataclass(frozen=True)
class NoticeTiming:
    default_duration_seconds: float = 5.0

    def __post_init__(self):
        value = self.default_duration_seconds
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or value < 0):
            raise ValueError("default notice duration must be finite and nonnegative")

    def automatic_dismiss_after(self, notice: Notice) -> float | None:
        """None means never automatically dismiss.

        Zero means awaiting acknowledgement; errors are never timed.
        A non-dismissible notice may only be cleared by its owning state.
        """
        if notice.severity == "error" or not notice.dismissible:
            return None
        seconds = (self.default_duration_seconds if notice.duration_seconds is None
                   else notice.duration_seconds)
        return seconds if seconds > 0 else None

    def encoder_can_confirm(self, notice: Notice) -> bool:
        """Only a dismissible persistent notice can be confirmed by encoder.

        Notice routing and input handling are deliberately out of scope.
        """
        return (notice.dismissible and
                self.automatic_dismiss_after(notice) is None)
