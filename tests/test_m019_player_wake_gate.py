"""Offline-only checks of player wake ordering and fail-closed behavior."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from disc_startup import CDS_DISC_OK
from player_service import wake_before_new_play


class PlayerWakeGateTests(unittest.TestCase):

    def make_power(self, waiting=True, wake_ok=True):
        policy = SimpleNamespace(play_must_wait=waiting)
        power = SimpleNamespace(idle_policy=policy)
        power.activity = Mock()

        def wake(*, verify):
            if not wake_ok:
                return False
            return verify("/dev/sr0")

        power.wake = Mock(side_effect=wake)
        return power

    def make_monitor(self):
        toc = SimpleNamespace(tracks=(1, 2, 3))
        return SimpleNamespace(
            device="/dev/sr0",
            identity="known-drive",
            loaded_toc=toc,
            path_exists=lambda dev: True,
            identity_reader=lambda dev: "known-drive",
            media=lambda dev: CDS_DISC_OK,
            read_toc=lambda dev: toc,
        )

    def test_feature_disabled_keeps_existing_behavior(self):
        self.assertTrue(wake_before_new_play(
            None, None, {"command": "play-selected"}, stopped=True
        ))

    def test_status_does_not_wake(self):
        power = self.make_power()
        self.assertTrue(wake_before_new_play(
            power, None, {"command": "status"}, stopped=True
        ))
        power.activity.assert_not_called()
        power.wake.assert_not_called()

    def test_active_playback_does_not_wake_again(self):
        power = self.make_power()
        self.assertTrue(wake_before_new_play(
            power, None, {"command": "playpause"}, stopped=False
        ))
        power.wake.assert_not_called()

    def test_normal_ready_play_needs_no_motor_start(self):
        power = self.make_power(waiting=False)
        self.assertTrue(wake_before_new_play(
            power, self.make_monitor(),
            {"command": "play-selected"}, stopped=True
        ))
        power.activity.assert_called_once()
        power.wake.assert_not_called()

    def test_verified_wake_allows_existing_play_guard(self):
        power = self.make_power()
        self.assertTrue(wake_before_new_play(
            power, self.make_monitor(),
            {"command": "play-selected"}, stopped=True
        ))
        power.wake.assert_called_once()

    def test_player_gate_uses_bounded_wake_wait(self):
        from unittest import mock

        power = self.make_power()
        monitor = self.make_monitor()

        with mock.patch(
            "player_service.wait_for_woken_disc",
            return_value=True,
        ) as wait:
            self.assertTrue(
                wake_before_new_play(
                    power,
                    monitor,
                    {"command": "play-selected"},
                    stopped=True,
                )
            )

        power.activity.assert_called_once()
        power.wake.assert_called_once()
        wait.assert_called_once()
        self.assertEqual(wait.call_args.args, (monitor, "/dev/sr0"))
        self.assertIsInstance(
            wait.call_args.kwargs["diagnostics"], dict
        )

    def test_failed_wake_blocks_play(self):
        power = self.make_power(wake_ok=False)
        self.assertFalse(wake_before_new_play(
            power, self.make_monitor(),
            {"command": "play-selected"}, stopped=True
        ))

    def test_missing_monitor_blocks_sleeping_drive_play(self):
        power = self.make_power()
        self.assertFalse(wake_before_new_play(
            power, None,
            {"command": "play-selected"}, stopped=True
        ))
        power.wake.assert_not_called()

    def test_changed_usb_identity_blocks_play(self):
        power = self.make_power()
        monitor = self.make_monitor()
        monitor.identity_reader = lambda dev: "other-drive"
        self.assertFalse(wake_before_new_play(
            power, monitor,
            {"command": "play-selected"}, stopped=True
        ))

    def test_changed_disc_blocks_play(self):
        from unittest import mock

        power = self.make_power()
        monitor = self.make_monitor()

        with mock.patch(
            "player_service.wait_for_woken_disc",
            return_value=False,
        ) as wait:
            self.assertFalse(
                wake_before_new_play(
                    power,
                    monitor,
                    {"command": "play-selected"},
                    stopped=True,
                )
            )

        power.wake.assert_called_once()
        wait.assert_called_once()
        self.assertEqual(wait.call_args.args, (monitor, "/dev/sr0"))
        self.assertIsInstance(
            wait.call_args.kwargs["diagnostics"], dict
        )


if __name__ == "__main__":
    unittest.main()
