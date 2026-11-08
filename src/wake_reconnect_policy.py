"""Pure M0.19 reconnect eligibility assessment.

No USB, optical-drive, motor or Sonos I/O.
A candidate assessment never authorizes PLAY.
"""

from dataclasses import dataclass
from enum import Enum

from drive_identity import UsbDriveId


class RecoveryState(str, Enum):
    BLOCKED = "blocked"
    CANDIDATE = "candidate"


@dataclass(frozen=True)
class RecoveryAssessment:
    state: RecoveryState
    reason: str

    @property
    def playback_authorized(self):
        return False


def assess_reconnect(
    *,
    original_identity,
    observed_identities,
    usb_disconnect_observed,
    same_disc_verified,
):
    """Assess supplied evidence without touching hardware."""

    def blocked(reason):
        return RecoveryAssessment(
            RecoveryState.BLOCKED, reason
        )

    if not isinstance(original_identity, UsbDriveId):
        return blocked("original_identity_unavailable")

    if type(usb_disconnect_observed) is not bool:
        return blocked("disconnect_evidence_invalid")

    if not usb_disconnect_observed:
        return blocked("no_disconnect_observed")

    if not isinstance(observed_identities, (list, tuple)):
        return blocked("observations_invalid")

    if any(
        not isinstance(item, UsbDriveId)
        for item in observed_identities
    ):
        return blocked("observations_invalid")

    matches = [
        item for item in observed_identities
        if item == original_identity
    ]

    if not matches:
        return blocked("original_drive_missing")

    if len(matches) != 1:
        return blocked("original_drive_ambiguous")

    if same_disc_verified is not True:
        return blocked("original_disc_unverified")

    return RecoveryAssessment(
        RecoveryState.CANDIDATE,
        "original_drive_and_disc_verified",
    )
