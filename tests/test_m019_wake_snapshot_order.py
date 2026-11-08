"""M0.19 wake snapshot ordering; strictly offline."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from player_service import (
    process_drive_activity,
    wake_before_new_play,
)


class WakeSnapshotOrderTests(unittest.TestCase):

    def make_power(self, *, waiting=True, wake_ok=True):
        events = []
        policy = SimpleNamespace(
            play_must_wait=waiting,
            wake_in_progress=waiting,
        )
        power = SimpleNamespace(idle_policy=policy)

        def activity():
            events.append("activity")

        def wake(*, verify):
            events.append("wake")
            self.assertIn("snapshot", events)
            self.assertLess(
                events.index("snapshot"), events.index("wake")
            )
            return wake_ok

        power.activity = Mock(side_effect=activity)
        power.wake = Mock(side_effect=wake)
        return power, events

    def test_play_publishes_before_wake(self):
        power, events = self.make_power()

        def publish():
            self.assertTrue(
                power.idle_policy.wake_in_progress
            )
            events.append("snapshot")

        result = wake_before_new_play(
            power, object(),
            {"command": "play-selected"},
            stopped=True,
            publish_wake=publish,
        )

        self.assertTrue(result)
        self.assertEqual(
            events, ["activity", "snapshot", "wake"]
        )

    def test_drive_activity_publishes_before_wake(self):
        power, events = self.make_power()

        result = process_drive_activity(
            power, object(),
            publish_wake=lambda: events.append("snapshot"),
        )

        self.assertTrue(result)
        self.assertEqual(
            events, ["activity", "snapshot", "wake"]
        )

    def test_failed_wake_still_blocks_play(self):
        power, events = self.make_power(wake_ok=False)

        result = wake_before_new_play(
            power, object(),
            {"command": "play"},
            stopped=True,
            publish_wake=lambda: events.append("snapshot"),
        )

        self.assertFalse(result)
        self.assertEqual(
            events, ["activity", "snapshot", "wake"]
        )

    def test_normal_activity_does_not_publish_wake(self):
        power, events = self.make_power(waiting=False)
        publish = Mock()

        self.assertTrue(
            process_drive_activity(
                power, object(), publish_wake=publish
            )
        )

        publish.assert_not_called()
        power.wake.assert_not_called()
        self.assertEqual(events, ["activity"])

    def test_no_monitor_blocks_without_start(self):
        power, events = self.make_power()
        publish = Mock()

        self.assertFalse(
            process_drive_activity(
                power, None, publish_wake=publish
            )
        )

        publish.assert_not_called()
        power.wake.assert_not_called()
        self.assertEqual(events, ["activity"])

    def test_status_never_triggers_wake(self):
        power, events = self.make_power()
        publish = Mock()

        self.assertTrue(
            wake_before_new_play(
                power, object(),
                {"command": "status"},
                stopped=True,
                publish_wake=publish,
            )
        )

        publish.assert_not_called()
        power.activity.assert_not_called()
        power.wake.assert_not_called()
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
