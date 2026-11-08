"""Deterministic notice lifecycle: priority, active-time countdown, acknowledgement.

The host passes monotonic timestamps and current base view explicitly. This
module never reads clocks or accesses display, GPIO, network or player APIs.
"""
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .notice import Notice
from .notice_policy import NoticeTiming


@dataclass(frozen=True)
class NoticePresentation:
    """Either the active notice or the caller's *current* underlying view."""
    notice_id: int | None
    notice: Notice | None
    base_view: Any

    @property
    def showing_notice(self) -> bool:
        return self.notice is not None


@dataclass
class _Entry:
    ident: int
    notice: Notice
    remaining: float | None
    started_at: float | None = None


class NoticeEngine:
    """Pure priority queue; larger priority wins, equal priority is FIFO.

    Only the visible notice uses its allotted display time. If a higher
    priority notice arrives, the interrupted notice resumes with its remaining
    time. Persistent notices are dismissed with confirm() when allowed;
    the source can always clear its own notice with clear().
    """

    def __init__(self, *, timing: NoticeTiming | None = None):
        self.timing = timing if timing is not None else NoticeTiming()
        self._entries: dict[int, _Entry] = {}
        self._next_id = 1
        self._last_now: float | None = None

    def _time(self, now: float) -> float:
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not isfinite(now):
            raise ValueError("now must be a finite monotonic timestamp")
        now = float(now)
        if self._last_now is not None and now < self._last_now:
            raise ValueError("time must not move backwards")
        self._last_now = now
        return now

    def _winner(self) -> _Entry | None:
        if not self._entries:
            return None
        return min(self._entries.values(),
                   key=lambda entry: (-entry.notice.priority, entry.ident))

    def _advance(self, now: float) -> None:
        """Charge elapsed active time, then activate the next eligible entry."""
        now = self._time(now)
        while True:
            active = self._winner()
            if active is None:
                return
            if active.started_at is None:
                active.started_at = now
            if active.remaining is None:
                return
            elapsed = max(0.0, now - active.started_at)
            if elapsed < active.remaining:
                active.remaining -= elapsed
                active.started_at = now
                return
            expiry = active.started_at + active.remaining
            del self._entries[active.ident]
            following = self._winner()
            if following is not None and following.started_at is None:
                # No queued notice consumes time before it first becomes visible.
                following.started_at = now
            # Remaining active time is never transferred to another notice.
            if following is None:
                return

    def _pause_active(self, now: float) -> None:
        active = self._winner()
        if active is None or active.started_at is None:
            return
        if active.remaining is not None:
            active.remaining = max(0.0, active.remaining - (now - active.started_at))
        active.started_at = None

    def post(self, notice: Notice, *, now: float) -> int:
        """Queue a notice and return its independent identifier."""
        if not isinstance(notice, Notice):
            raise TypeError("expected Notice")
        self._advance(now)
        old = self._winner()
        ident = self._next_id
        self._next_id += 1
        self._entries[ident] = _Entry(
            ident, notice, self.timing.automatic_dismiss_after(notice))
        new = self._winner()
        if old is not None and old is not new:
            self._pause_entry(old, float(now))
        if new is not None and new.started_at is None:
            new.started_at = float(now)
        return ident

    @staticmethod
    def _pause_entry(entry: _Entry, now: float) -> None:
        if entry.started_at is not None:
            if entry.remaining is not None:
                entry.remaining = max(0.0, entry.remaining - (now - entry.started_at))
            entry.started_at = None

    def presentation(self, *, now: float, base_view: Any) -> NoticePresentation:
        """Return an overlay or the current base view, without storing it."""
        self._advance(now)
        active = self._winner()
        if active is None:
            return NoticePresentation(None, None, base_view)
        return NoticePresentation(active.ident, active.notice, base_view)

    def confirm(self, *, now: float) -> bool:
        """Accept encoder confirmation only for a persistent dismissible notice."""
        self._advance(now)
        active = self._winner()
        if active is None or not self.timing.encoder_can_confirm(active.notice):
            return False
        del self._entries[active.ident]
        successor = self._winner()
        if successor is not None and successor.started_at is None:
            successor.started_at = float(now)
        return True

    def clear(self, notice_id: int, *, now: float) -> bool:
        """Remove one source-owned notice, including non-dismissible notices."""
        self._advance(now)
        if notice_id not in self._entries:
            return False
        was_active = self._winner() is self._entries[notice_id]
        del self._entries[notice_id]
        if was_active:
            successor = self._winner()
            if successor is not None and successor.started_at is None:
                successor.started_at = float(now)
        return True

    def pending_count(self, *, now: float) -> int:
        self._advance(now)
        return len(self._entries)
