"""Pure M0.19 idle optical-drive policy; no hardware, GPIO or Sonos I/O.

An acknowledgement is required before treating a motor as stopped/awake.
The caller must serialize drive operations with CD reading and never infer
that a successful START command proves the disc is readable.

A failed wake latches a fault. The latch is lifted for another *verified* wake
attempt only by a fresh user action after ``retry_cooldown_seconds``, at most
``max_wake_failures`` times in a row; a verified wake resets the count. A
latched fault never authorizes PLAY, only another full wake verification does.
"""
from dataclasses import dataclass
import math


@dataclass
class IdleDrivePolicy:
    idle_seconds: float = 300.0
    last_activity: float = 0.0
    spun_down: bool = False
    stop_attempted: bool = False
    wake_in_progress: bool = False
    wake_fault_latched: bool = False
    # A USB drive usually re-enumerates within seconds and a verified wake may
    # take up to ~20 s, so one minute separates attempts without leaving the
    # user waiting long; three failures in a row mean a real fault.
    retry_cooldown_seconds: float = 60.0
    max_wake_failures: int = 3
    wake_failures: int = 0
    fault_at: float | None = None

    def __post_init__(self):
        if type(self.idle_seconds) not in (int, float) or not 30 <= self.idle_seconds <= 86400:
            raise ValueError('idle_seconds must be between 30 and 86400')
        if (type(self.retry_cooldown_seconds) not in (int, float)
                or not 5 <= self.retry_cooldown_seconds <= 3600):
            raise ValueError('retry_cooldown_seconds must be between 5 and 3600')
        if type(self.max_wake_failures) is not int or not 1 <= self.max_wake_failures <= 10:
            raise ValueError('max_wake_failures must be between 1 and 10')

    def activity(self, now):
        """One debounced button press or encoder detent, including PLAY.

        Return 'wake' only if we know a previous STOP was acknowledged.
        PLAY must not be forwarded until wake and disc readiness are proven.
        """
        self.last_activity = now
        # A failed wake is retried only by a fresh user action, only after the
        # cooldown and only a bounded number of times. Keepalive and idle
        # checks never retry on their own.
        if self.wake_fault_latched:
            if not self.wake_retry_due(now):
                return None
            self.wake_fault_latched = False
        # Retain STOP uncertainty until a verified wake succeeds.
        # A failed STOP must not be cleared by physical input.
        if self.spun_down and not self.wake_in_progress:
            self.wake_in_progress = True
            return 'wake'
        return None

    def keepalive_allowed(self, now):
        """Continue existing keepalive *until* the idle deadline.

        Motor keepalive pulses do not reset last_activity. After a STOP
        attempt (even with an uncertain result), cease keepalive until a
        fresh user event creates a new activity epoch.
        """
        return (not self.wake_fault_latched and not self.spun_down and not self.stop_attempted
                and not self.wake_in_progress
                and now - self.last_activity < self.idle_seconds)

    def idle_action(self, now, *, playback_active, reader_active,
                    group_cleanup_pending=False, drive_ready=True):
        """Advise one STOP only when every local activity guard is clear."""
        if playback_active or reader_active or group_cleanup_pending:
            self.last_activity = now
            return None
        if (not drive_ready or self.wake_fault_latched or self.spun_down or self.wake_in_progress
                or self.stop_attempted):
            return None
        if now - self.last_activity < self.idle_seconds:
            return None
        self.stop_attempted = True
        return 'stop'

    def stop_result(self, *, acknowledged):
        """Unknown/failed STOP result must never be classified as spun down."""
        if acknowledged is True:
            self.spun_down = True

    def stop_not_sent(self):
        """The STOP never reached the drive (device lock busy).

        Nothing is uncertain, so the attempt is forgotten: PLAY stays allowed and
        the next idle check may advise STOP again. Only a STOP that was sent and
        not acknowledged stays latched via ``stop_result(acknowledged=False)``.
        """
        self.stop_attempted = False

    def wake_result(self, now, *, drive_verified):
        """START acknowledgement alone is insufficient: verify disc/drive."""
        self.wake_in_progress = False
        if drive_verified is not True:
            self.wake_fault_latched = True
            self.wake_failures += 1
            self.fault_at = now
            return False
        if drive_verified is True:
            self.spun_down = False
            self.last_activity = now
            self.stop_attempted = False
            self.wake_failures = 0
            self.fault_at = None
            return True
        return False

    def wake_retry_due(self, now):
        """True when a latched wake fault may be retried by the next user action."""
        return (self.wake_fault_latched and self.spun_down
                and self.wake_failures < self.max_wake_failures
                and self.fault_at is not None and now >= self.fault_at
                and now - self.fault_at >= self.retry_cooldown_seconds)

    def retry_state(self, now):
        """(seconds until a retry is allowed or None, retries exhausted) for display."""
        if not self.wake_fault_latched:
            return None, False
        if self.wake_failures >= self.max_wake_failures:
            return None, True
        if self.fault_at is None:
            return None, False
        left = self.retry_cooldown_seconds - (now - self.fault_at)
        return max(0, math.ceil(left)), False

    def reset_after_eject(self, now):
        """The disc that failed verification is gone: start from a clean state."""
        self.spun_down = False
        self.stop_attempted = False
        self.wake_in_progress = False
        self.wake_fault_latched = False
        self.wake_failures = 0
        self.fault_at = None
        self.last_activity = now

    @property
    def play_must_wait(self):
        return self.spun_down or self.wake_in_progress or self.wake_fault_latched
