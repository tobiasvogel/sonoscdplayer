"""Fail-closed behavior after an unconfirmed idle STOP."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from drive_idle_policy import IdleDrivePolicy
from player_service import (
    eject_blocked_by_idle_policy,
    idle_stop_unverified,
    process_drive_activity,
    wake_before_new_play,
)


class UncertainStopTests(unittest.TestCase):

    def make_uncertain_power(self):
        policy = IdleDrivePolicy(
            idle_seconds=300,
            last_activity=0,
        )

        action = policy.idle_action(
            300,
            playback_active=False,
            reader_active=False,
        )
        self.assertEqual(action, 'stop')

        policy.stop_result(acknowledged=False)

        power = SimpleNamespace(idle_policy=policy)
        power.activity = Mock(
            side_effect=lambda: policy.activity(301)
        )
        power.wake = Mock(return_value=True)

        return power

    def test_button_does_not_clear_uncertain_stop(self):
        power = self.make_uncertain_power()

        power.activity()

        self.assertTrue(power.idle_policy.stop_attempted)
        self.assertFalse(power.idle_policy.spun_down)
        self.assertTrue(idle_stop_unverified(power))
        self.assertFalse(
            power.idle_policy.keepalive_allowed(302)
        )

    def test_uncertain_stop_does_not_retry_stop(self):
        power = self.make_uncertain_power()

        power.activity()

        action = power.idle_policy.idle_action(
            700,
            playback_active=False,
            reader_active=False,
        )

        self.assertIsNone(action)
        self.assertTrue(power.idle_policy.stop_attempted)

    def test_uncertain_stop_blocks_eject(self):
        power = self.make_uncertain_power()

        power.activity()

        self.assertTrue(
            eject_blocked_by_idle_policy(power)
        )

    def test_uncertain_stop_blocks_play_without_wake(self):
        power = self.make_uncertain_power()

        result = wake_before_new_play(
            power,
            object(),
            {'command': 'play-selected'},
            stopped=True,
        )

        self.assertFalse(result)
        power.wake.assert_not_called()

    def test_uncertain_stop_blocks_activity_without_wake(self):
        power = self.make_uncertain_power()

        result = process_drive_activity(
            power, object()
        )

        self.assertFalse(result)
        power.wake.assert_not_called()

    def test_acknowledged_stop_can_be_verified_and_cleared(self):
        policy = IdleDrivePolicy(
            idle_seconds=300, last_activity=0
        )

        self.assertEqual(
            policy.idle_action(
                300,
                playback_active=False,
                reader_active=False,
            ),
            'stop',
        )

        policy.stop_result(acknowledged=True)

        self.assertEqual(policy.activity(301), 'wake')
        self.assertTrue(policy.stop_attempted)
        self.assertTrue(policy.spun_down)

        self.assertTrue(
            policy.wake_result(302, drive_verified=True)
        )
        self.assertFalse(policy.stop_attempted)
        self.assertFalse(policy.spun_down)


if __name__ == '__main__':
    unittest.main()
