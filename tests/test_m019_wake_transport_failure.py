"""M0.19 START transport failure: strictly offline regression."""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from drive_idle_policy import IdleDrivePolicy
from drive_power import DrivePowerManager
from drive_identity import UsbDriveId
from wake_reconnect_policy import assess_reconnect, RecoveryState


ORIGINAL = UsbDriveId("152d", "0583", "test-serial")


@contextmanager
def fake_drive_lock(device, *, blocking=True):
    assert device == "/dev/sr0"
    yield True


class WakeTransportFailureTests(unittest.TestCase):

    def make_power(self):
        commands = []

        def runner(args, **kwargs):
            commands.append(tuple(args))
            if args[:2] == ["sg_start", "--start"]:
                return SimpleNamespace(
                    returncode=35,
                    stdout="",
                    stderr=(
                        "start stop unit: transport: "
                        "Host_status=0x07 [DID_ERROR]\n"
                        "Transport error"
                    ),
                )
            raise AssertionError(f"Unexpected command: {args}")

        power = DrivePowerManager(
            device="/dev/sr0",
            config={
                "enabled": True,
                "strategy": "auto",
                "interval": 10.0,
                "idle_spin_down_seconds": 30.0,
                "overrides": {},
            },
            runner=runner,
            clock=lambda: 100.0,
        )
        power.active_strategy = "keepalive"
        power.supported["start_stop_unit"] = True
        power.last_start = 50.0
        power.idle_policy = IdleDrivePolicy(
            idle_seconds=30,
            last_activity=50.0,
            spun_down=True,
            stop_attempted=True,
        )
        return power, commands

    @patch("drive_power.drive_lock", fake_drive_lock)
    def test_exit35_blocks_wake_without_verification(self):
        power, commands = self.make_power()
        verify = Mock(return_value=True)

        self.assertFalse(power.wake(verify=verify))
        verify.assert_not_called()

        self.assertEqual(
            commands,
            [("sg_start", "--start", "/dev/sr0")],
        )
        self.assertTrue(power.idle_policy.spun_down)
        self.assertTrue(power.idle_policy.wake_fault_latched)
        self.assertIn("exit 35", power.last_error)
        self.assertIn("DID_ERROR", power.last_error)

    @patch("drive_power.drive_lock", fake_drive_lock)
    def test_second_wake_cannot_retry_start(self):
        power, commands = self.make_power()
        verify = Mock(return_value=True)

        self.assertFalse(power.wake(verify=verify))
        self.assertFalse(power.wake(verify=verify))

        verify.assert_not_called()
        self.assertEqual(
            commands,
            [("sg_start", "--start", "/dev/sr0")],
        )

    @patch("drive_power.drive_lock", fake_drive_lock)
    def test_reconnect_candidate_does_not_override_fault(self):
        power, commands = self.make_power()
        verify = Mock(return_value=True)

        self.assertFalse(power.wake(verify=verify))

        assessment = assess_reconnect(
            original_identity=ORIGINAL,
            observed_identities=[ORIGINAL],
            usb_disconnect_observed=True,
            same_disc_verified=True,
        )

        self.assertEqual(
            assessment.state, RecoveryState.CANDIDATE
        )
        self.assertFalse(assessment.playback_authorized)

        self.assertTrue(power.idle_policy.wake_fault_latched)
        self.assertFalse(power.wake(verify=verify))
        verify.assert_not_called()
        self.assertEqual(len(commands), 1)

    @patch("drive_power.drive_lock", fake_drive_lock)
    def test_failure_keeps_idle_stop_state(self):
        power, commands = self.make_power()

        self.assertFalse(power.wake(verify=lambda device: True))

        self.assertTrue(power.idle_policy.stop_attempted)
        self.assertTrue(power.idle_policy.spun_down)
        self.assertFalse(power.idle_policy.wake_in_progress)
        self.assertTrue(power.idle_policy.play_must_wait)
        self.assertEqual(len(commands), 1)


if __name__ == "__main__":
    unittest.main()
