"""Pure M0.19 recovery evidence coordinator.

Consumes previously collected observations; performs no I/O.
An accepted evidence assessment is NOT a successful motor wake.
Never clears a wake fault or authorizes PLAY/EJECT.
"""

from dataclasses import dataclass
from enum import Enum

from drive_identity import UsbDriveId
from wake_reconnect_observer import (
    DeviceObservation,
    ObservationResult,
    ObservationState,
)
from wake_reconnect_policy import (
    RecoveryState,
    assess_reconnect,
)


class CoordinationState(str, Enum):
    BLOCKED = "blocked"
    EVIDENCE_COMPLETE = "evidence_complete"


@dataclass(frozen=True)
class CoordinationResult:
    state: CoordinationState
    reason: str
    device: str | None = None

    @property
    def playback_authorized(self):
        return False

    @property
    def wake_fault_clearable(self):
        return False

    @property
    def motor_command_authorized(self):
        return False


def coordinate_reconnect(
    *,
    original_identity,
    observation,
    current_devices,
    same_disc_verified,
    start_transport_failed,
    wake_fault_latched,
):
    """Assess supplied facts without changing any system state."""

    def blocked(reason):
        return CoordinationResult(
            CoordinationState.BLOCKED, reason
        )

    if type(start_transport_failed) is not bool:
        return blocked("invalid_transport_evidence")

    if start_transport_failed is not True:
        return blocked("no_start_transport_failure")

    if type(wake_fault_latched) is not bool:
        return blocked("invalid_fault_state")

    if wake_fault_latched is not True:
        return blocked("wake_fault_not_latched")

    if not isinstance(original_identity, UsbDriveId):
        return blocked("original_identity_unavailable")

    if not isinstance(observation, ObservationResult):
        return blocked("invalid_observation")

    if observation.state != ObservationState.REAPPEARED:
        return blocked("reconnect_not_proven")

    if not isinstance(current_devices, (list, tuple)):
        return blocked("invalid_device_inventory")

    if any(
        not isinstance(device, DeviceObservation)
        or not isinstance(device.identity, UsbDriveId)
        or not isinstance(device.path, str)
        for device in current_devices
    ):
        return blocked("invalid_device_inventory")

    paths = [device.path for device in current_devices]
    if len(paths) != len(set(paths)):
        return blocked("duplicate_device_path")

    matching = [
        device for device in current_devices
        if device.identity == original_identity
    ]

    if len(matching) != 1:
        return blocked("original_identity_not_unique")

    if matching[0].path != observation.device:
        return blocked("reappeared_device_path_mismatch")

    assessment = assess_reconnect(
        original_identity=original_identity,
        observed_identities=[
            device.identity for device in current_devices
        ],
        usb_disconnect_observed=True,
        same_disc_verified=same_disc_verified,
    )

    if assessment.state != RecoveryState.CANDIDATE:
        return blocked(assessment.reason)

    return CoordinationResult(
        CoordinationState.EVIDENCE_COMPLETE,
        "reconnect_evidence_complete_not_authorized",
        observation.device,
    )
