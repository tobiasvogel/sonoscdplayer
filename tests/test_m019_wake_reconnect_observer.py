"""Offline M0.19 USB reconnect sequence tests."""
import unittest

from drive_identity import UsbDriveId
from wake_reconnect_observer import (
    DeviceObservation,
    ObservationState,
    ReconnectObserver,
)


ORIGINAL = UsbDriveId("152d", "0583", "test-serial")
OTHER = UsbDriveId("152d", "0583", "other-serial")

A = DeviceObservation("/dev/sr0", ORIGINAL)
B = DeviceObservation("/dev/sr1", ORIGINAL)
FOREIGN = DeviceObservation("/dev/sr0", OTHER)


class ReconnectObserverTests(unittest.TestCase):

    def make(self):
        return ReconnectObserver(
            ORIGINAL, max_seconds=10.0
        )

    def test_original_drive_reappears(self):
        o = self.make()
        self.assertEqual(
            o.record(elapsed_seconds=0, devices=[A]).state,
            ObservationState.WAITING,
        )
        o.record(elapsed_seconds=1, devices=[])
        result = o.record(elapsed_seconds=2, devices=[A])
        self.assertEqual(result.state, ObservationState.REAPPEARED)
        self.assertFalse(result.playback_authorized)

    def test_device_renumbering(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[A])
        o.record(elapsed_seconds=1, devices=[])
        result = o.record(elapsed_seconds=2, devices=[B])
        self.assertEqual(result.state, ObservationState.REAPPEARED)
        self.assertEqual(result.device, "/dev/sr1")

    def test_no_disconnect_no_recovery(self):
        o = self.make()
        for t in (0, 1, 2):
            result = o.record(elapsed_seconds=t, devices=[A])
        self.assertEqual(result.state, ObservationState.WAITING)

    def test_foreign_drive_never_satisfies_recovery(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[])
        result = o.record(
            elapsed_seconds=2, devices=[FOREIGN]
        )
        self.assertEqual(result.state, ObservationState.WAITING)

    def test_ambiguous_original_identity_blocks(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[])
        result = o.record(elapsed_seconds=2, devices=[A, B])
        self.assertEqual(result.state, ObservationState.BLOCKED)

    def test_timeout_blocks(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[])
        result = o.record(elapsed_seconds=11, devices=[A])
        self.assertEqual(result.state, ObservationState.BLOCKED)

    def test_incomplete_inventory_blocks(self):
        o = self.make()
        result = o.record(elapsed_seconds=0, devices=None)
        self.assertEqual(result.state, ObservationState.BLOCKED)

    def test_time_reversal_blocks(self):
        o = self.make()
        o.record(elapsed_seconds=3, devices=[])
        result = o.record(elapsed_seconds=2, devices=[A])
        self.assertEqual(result.state, ObservationState.BLOCKED)

    def test_terminal_result_is_latched(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[A])
        o.record(elapsed_seconds=1, devices=[])
        first = o.record(elapsed_seconds=2, devices=[A])
        second = o.record(elapsed_seconds=3, devices=[FOREIGN])
        self.assertEqual(first, second)

    def test_initial_absence_does_not_prove_disconnect(self):
        o = self.make()
        first = o.record(elapsed_seconds=0, devices=[])
        second = o.record(elapsed_seconds=1, devices=[A])

        self.assertEqual(first.state, ObservationState.WAITING)
        self.assertEqual(
            first.reason, "awaiting_original_presence"
        )
        self.assertEqual(second.state, ObservationState.WAITING)
        self.assertEqual(second.reason, "awaiting_disconnect")

    def test_initial_foreign_drive_does_not_prove_disconnect(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[FOREIGN])
        result = o.record(elapsed_seconds=1, devices=[A])
        self.assertEqual(result.state, ObservationState.WAITING)

    def test_nonfinite_time_blocks(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                o = self.make()
                result = o.record(
                    elapsed_seconds=value, devices=[A]
                )
                self.assertEqual(
                    result.state, ObservationState.BLOCKED
                )

    def test_no_observation_authorizes_playback(self):
        o = self.make()
        results = [
            o.record(elapsed_seconds=0, devices=[]),
            o.record(elapsed_seconds=1, devices=[A]),
        ]
        self.assertTrue(
            all(not r.playback_authorized for r in results)
        )


if __name__ == "__main__":
    unittest.main()
