"""Pure M0.19 USB reconnect sequence observer.

Consumes complete, externally collected device snapshots.
No hardware access, motor commands, CD reads or playback.
"""
from dataclasses import dataclass
from enum import Enum
import math
import re

from drive_identity import UsbDriveId


class ObservationState(str, Enum):
    WAITING = "waiting"
    REAPPEARED = "reappeared"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DeviceObservation:
    path: str
    identity: UsbDriveId


@dataclass(frozen=True)
class ObservationResult:
    state: ObservationState
    reason: str
    device: str | None = None

    @property
    def playback_authorized(self):
        return False


class ReconnectObserver:
    """Track one bounded disappearance/reappearance sequence.

    A snapshot must represent a complete device enumeration.
    Unknown or incomplete observations must not be supplied as [].
    """

    def __init__(self, original_identity, *, max_seconds=10.0):
        if not isinstance(original_identity, UsbDriveId):
            raise ValueError("original USB identity required")
        if (
            type(max_seconds) not in (int, float)
            or not 0 < max_seconds <= 30
        ):
            raise ValueError("invalid observation window")

        self.original_identity = original_identity
        self.max_seconds = max_seconds
        self._present_seen = False
        self._missing_seen = False
        self._last_elapsed = None
        self._result = ObservationResult(
            ObservationState.WAITING, "awaiting_disconnect"
        )

    def record(self, *, elapsed_seconds, devices):
        """Consume one complete synthetic USB inventory."""
        if self._result.state != ObservationState.WAITING:
            return self._result

        if (
            type(elapsed_seconds) not in (int, float)
            or not math.isfinite(elapsed_seconds)
            or not 0 <= elapsed_seconds
            or (
                self._last_elapsed is not None
                and elapsed_seconds < self._last_elapsed
            )
        ):
            self._result = ObservationResult(
                ObservationState.BLOCKED,
                "invalid_observation_time",
            )
            return self._result

        self._last_elapsed = elapsed_seconds

        if elapsed_seconds > self.max_seconds:
            self._result = ObservationResult(
                ObservationState.BLOCKED,
                "observation_timeout",
            )
            return self._result

        if not isinstance(devices, (list, tuple)):
            self._result = ObservationResult(
                ObservationState.BLOCKED,
                "incomplete_observation",
            )
            return self._result

        if any(
            not isinstance(d, DeviceObservation)
            or not isinstance(d.identity, UsbDriveId)
            or not isinstance(d.path, str)
            or not re.fullmatch(r"/dev/sr[0-9]+", d.path)
            for d in devices
        ):
            self._result = ObservationResult(
                ObservationState.BLOCKED,
                "invalid_device_observation",
            )
            return self._result

        paths = [d.path for d in devices]
        if len(set(paths)) != len(paths):
            self._result = ObservationResult(
                ObservationState.BLOCKED,
                "duplicate_device_path",
            )
            return self._result

        matches = [
            d for d in devices
            if d.identity == self.original_identity
        ]

        if len(matches) > 1:
            self._result = ObservationResult(
                ObservationState.BLOCKED,
                "ambiguous_original_identity",
            )
            return self._result

        if not matches:
            if not self._present_seen:
                return ObservationResult(
                    ObservationState.WAITING,
                    "awaiting_original_presence",
                )
            self._missing_seen = True
            return ObservationResult(
                ObservationState.WAITING,
                "awaiting_reappearance",
            )

        if not self._missing_seen:
            self._present_seen = True
            return ObservationResult(
                ObservationState.WAITING,
                "awaiting_disconnect",
            )

        self._result = ObservationResult(
            ObservationState.REAPPEARED,
            "original_identity_reappeared",
            matches[0].path,
        )
        return self._result
