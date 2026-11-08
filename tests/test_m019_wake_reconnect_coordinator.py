"""M0.19 reconnect coordination: strictly simulated evidence."""

import unittest

from drive_identity import UsbDriveId
from wake_reconnect_observer import (
    DeviceObservation,
    ObservationResult,
    ObservationState,
    ReconnectObserver,
)
from wake_reconnect_coordinator import (
    CoordinationState,
    coordinate_reconnect,
)


ORIGINAL = UsbDriveId("152d", "0583", "test-serial")
OTHER = UsbDriveId("152d", "0583", "other-serial")

A = DeviceObservation("/dev/sr0", ORIGINAL)
B = DeviceObservation("/dev/sr1", ORIGINAL)
FOREIGN = DeviceObservation("/dev/sr0", OTHER)


def completed_observation(*, renumber=False):
    observer = ReconnectObserver(
        ORIGINAL, max_seconds=10
    )
    observer.record(elapsed_seconds=0, devices=[A])
    observer.record(elapsed_seconds=1, devices=[])
    return observer.record(
        elapsed_seconds=2,
        devices=[B if renumber else A],
    )


class ReconnectCoordinatorTests(unittest.TestCase):

    def assess(
        self,
        *,
        observation=None,
        devices=None,
        disc=True,
        transport=True,
        fault=True,
    ):
        if observation is None:
            observation = completed_observation()
        if devices is None:
            devices = [A]
        return coordinate_reconnect(
            original_identity=ORIGINAL,
            observation=observation,
            current_devices=devices,
            same_disc_verified=disc,
            start_transport_failed=transport,
            wake_fault_latched=fault,
        )

    def test_complete_evidence_is_not_authorization(self):
        result = self.assess()
        self.assertEqual(
            result.state,
            CoordinationState.EVIDENCE_COMPLETE,
        )
        self.assertEqual(result.device, "/dev/sr0")
        self.assertFalse(result.playback_authorized)
        self.assertFalse(result.wake_fault_clearable)
        self.assertFalse(result.motor_command_authorized)

    def test_renumbered_original_drive(self):
        result = self.assess(
            observation=completed_observation(
                renumber=True
            ),
            devices=[B],
        )
        self.assertEqual(
            result.state,
            CoordinationState.EVIDENCE_COMPLETE,
        )
        self.assertEqual(result.device, "/dev/sr1")

    def test_no_transport_error_blocks(self):
        result = self.assess(transport=False)
        self.assertEqual(
            result.reason,
            "no_start_transport_failure",
        )

    def test_unlatched_fault_blocks(self):
        result = self.assess(fault=False)
        self.assertEqual(
            result.reason,
            "wake_fault_not_latched",
        )

    def test_unverified_disc_blocks(self):
        result = self.assess(disc=False)
        self.assertEqual(
            result.reason,
            "original_disc_unverified",
        )

    def test_foreign_device_blocks(self):
        result = self.assess(devices=[FOREIGN])
        self.assertEqual(
            result.state,
            CoordinationState.BLOCKED,
        )

    def test_ambiguous_identity_blocks(self):
        result = self.assess(devices=[A, B])
        self.assertEqual(
            result.reason,
            "original_identity_not_unique",
        )

    def test_device_path_mismatch_blocks(self):
        result = self.assess(devices=[B])
        self.assertEqual(
            result.reason,
            "reappeared_device_path_mismatch",
        )

    def test_no_disconnect_blocks(self):
        observer = ReconnectObserver(ORIGINAL)
        waiting = observer.record(
            elapsed_seconds=0, devices=[A]
        )
        result = self.assess(observation=waiting)
        self.assertEqual(
            result.reason,
            "reconnect_not_proven",
        )

    def test_timeout_blocks(self):
        observer = ReconnectObserver(
            ORIGINAL, max_seconds=10
        )
        observer.record(
            elapsed_seconds=0, devices=[A]
        )
        timed_out = observer.record(
            elapsed_seconds=11, devices=[]
        )
        result = self.assess(
            observation=timed_out
        )
        self.assertEqual(
            result.state,
            CoordinationState.BLOCKED,
        )

    def test_duplicate_device_paths_block(self):
        result = self.assess(
            devices=[A, A]
        )
        self.assertEqual(
            result.reason,
            "duplicate_device_path",
        )

    def test_invalid_inventory_blocks(self):
        result = self.assess(devices=[None])
        self.assertEqual(
            result.reason,
            "invalid_device_inventory",
        )

    def test_all_results_keep_safety_barriers(self):
        for result in (
            self.assess(),
            self.assess(disc=False),
            self.assess(fault=False),
            self.assess(devices=[FOREIGN]),
        ):
            self.assertFalse(result.playback_authorized)
            self.assertFalse(result.wake_fault_clearable)
            self.assertFalse(result.motor_command_authorized)


if __name__ == "__main__":
    unittest.main()
