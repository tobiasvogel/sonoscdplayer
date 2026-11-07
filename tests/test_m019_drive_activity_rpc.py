"""Drive activity integration tests; no real drive or Sonos I/O."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from player_service import process_drive_activity


class DriveActivityRpcTests(unittest.TestCase):

    def make_power(self, *, waiting=False, wake_result=True):
        policy = SimpleNamespace(play_must_wait=waiting)
        power = SimpleNamespace(idle_policy=policy)
        power.activity = Mock()
        power.wake = Mock(return_value=wake_result)
        return power

    def test_disabled_feature_does_nothing(self):
        self.assertTrue(process_drive_activity(None, None))
        self.assertTrue(process_drive_activity(
            SimpleNamespace(idle_policy=None), None
        ))

    def test_normal_activity_only_updates_timer(self):
        power = self.make_power()
        self.assertTrue(process_drive_activity(power, None))
        power.activity.assert_called_once()
        power.wake.assert_not_called()

    def test_sleeping_drive_requires_monitor(self):
        power = self.make_power(waiting=True)
        self.assertFalse(process_drive_activity(power, None))
        power.wake.assert_not_called()

    def test_verified_wake_accepted(self):
        power = self.make_power(waiting=True)
        monitor = object()

        with patch(
            "player_service.wait_for_woken_disc",
            return_value=True,
        ) as verification:
            def wake(*, verify):
                return verify("/dev/sr0")

            power.wake.side_effect = wake

            self.assertTrue(
                process_drive_activity(power, monitor)
            )

        power.activity.assert_called_once()
        power.wake.assert_called_once()
        verification.assert_called_once_with(
            monitor, "/dev/sr0"
        )

    def test_failed_wake_rejected(self):
        power = self.make_power(
            waiting=True, wake_result=False
        )
        self.assertFalse(
            process_drive_activity(power, object())
        )
        power.wake.assert_called_once()

    def test_activity_never_calls_wake_without_need(self):
        power = self.make_power(waiting=False)
        for _ in range(3):
            self.assertTrue(
                process_drive_activity(power, object())
            )
        self.assertEqual(power.activity.call_count, 3)
        power.wake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
