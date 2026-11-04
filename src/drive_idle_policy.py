"""Pure M0.19 idle optical-drive policy; no hardware, GPIO or Sonos I/O.

An acknowledgement is required before treating a motor as stopped/awake.
The caller must serialize drive operations with CD reading and never infer
that a successful START command proves the disc is readable.
"""
from dataclasses import dataclass


@dataclass
class IdleDrivePolicy:
    idle_seconds: float = 300.0
    last_activity: float = 0.0
    spun_down: bool = False
    stop_attempted: bool = False
    wake_in_progress: bool = False

    def __post_init__(self):
        if type(self.idle_seconds) not in (int, float) or not 30 <= self.idle_seconds <= 86400:
            raise ValueError('idle_seconds must be between 30 and 86400')

    def activity(self, now):
        """One debounced button press or encoder detent, including PLAY.

        Return 'wake' only if we know a previous STOP was acknowledged.
        PLAY must not be forwarded until wake and disc readiness are proven.
        """
        self.last_activity = now
        self.stop_attempted = False
        if self.spun_down and not self.wake_in_progress:
            self.wake_in_progress = True
            return 'wake'
        return None

    def idle_action(self, now, *, playback_active, reader_active,
                    group_cleanup_pending=False, drive_ready=True):
        """Advise one STOP only when every local activity guard is clear."""
        if playback_active or reader_active or group_cleanup_pending:
            self.last_activity = now
            return None
        if (not drive_ready or self.spun_down or self.wake_in_progress
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

    def wake_result(self, now, *, drive_verified):
        """START acknowledgement alone is insufficient: verify disc/drive."""
        self.wake_in_progress = False
        if drive_verified is True:
            self.spun_down = False
            self.last_activity = now
            self.stop_attempted = False
            return True
        return False

    @property
    def play_must_wait(self):
        return self.spun_down or self.wake_in_progress
