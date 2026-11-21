"""Drive wake-up: verification, disc readiness, diagnostics, fault recovery and concurrent wake requests (fake drive)."""
import json
import multiprocessing
import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from sonoscdplayer.player import service as player_service
from sonoscdplayer.drive.disc_startup import CDS_DISC_OK, CDS_NO_DISC, CDS_DRIVE_NOT_READY
from sonoscdplayer.drive.wake_validation import verify_woken_disc, wait_for_woken_disc
from sonoscdplayer.drive.idle_policy import IdleDrivePolicy
from sonoscdplayer.ui.display_service import (
    drive_fault_details,
    drive_notice_frame,
    modern_status_frame,
    selected_status_frame,
    drive_waking,
    drive_fault,
    request_status,
)
from sonoscdplayer.player.service import (
    wake_before_new_play,
    process_drive_activity,
    StatusPublisher,
    response,
)
from sonoscdplayer.player.state import Mode
from sonoscdplayer.drive.power import DrivePowerManager



class WakeValidationTests(unittest.TestCase):
    def make_monitor(self, **changes):
        toc = SimpleNamespace(tracks=(1, 2, 3))
        values = dict(
            device="/dev/sr0",
            identity="verified-usb-drive",
            loaded_toc=toc,
            path_exists=lambda device: True,
            identity_reader=lambda device: "verified-usb-drive",
            media=lambda device: CDS_DISC_OK,
            read_toc=lambda device: toc,
        )
        values.update(changes)
        return SimpleNamespace(**values)

    def test_same_drive_and_disc_accepted(self):
        self.assertTrue(
            verify_woken_disc(self.make_monitor(), "/dev/sr0")
        )

    def test_wrong_device_path_rejected(self):
        self.assertFalse(
            verify_woken_disc(self.make_monitor(), "/dev/sr1")
        )

    def test_missing_usb_identity_rejected(self):
        self.assertFalse(
            verify_woken_disc(
                self.make_monitor(identity=None), "/dev/sr0"
            )
        )

    def test_different_usb_identity_rejected(self):
        monitor = self.make_monitor(
            identity_reader=lambda device: "other-drive"
        )
        self.assertFalse(verify_woken_disc(monitor, "/dev/sr0"))

    def test_missing_device_rejected(self):
        monitor = self.make_monitor(
            path_exists=lambda device: False
        )
        self.assertFalse(verify_woken_disc(monitor, "/dev/sr0"))

    def test_no_disc_rejected(self):
        monitor = self.make_monitor(
            media=lambda device: CDS_NO_DISC
        )
        self.assertFalse(verify_woken_disc(monitor, "/dev/sr0"))

    def test_different_disc_is_a_disc_change_not_a_fault(self):
        # A CD swapped with the drive's own eject button while it slept.
        new = SimpleNamespace(tracks=(1, 2, 3, 4))
        monitor = self.make_monitor(read_toc=lambda device: new)
        self.assertTrue(verify_woken_disc(monitor, "/dev/sr0"))
        self.assertIs(monitor.changed_disc, new)

    def test_unreadable_disc_rejected(self):
        def fail(device):
            raise OSError("disc unreadable")

        monitor = self.make_monitor(read_toc=fail)
        self.assertFalse(verify_woken_disc(monitor, "/dev/sr0"))

    def test_unknown_original_toc_rejected(self):
        monitor = self.make_monitor(loaded_toc=None)
        self.assertFalse(verify_woken_disc(monitor, "/dev/sr0"))


class WakeWaitTests(unittest.TestCase):

    def make_monitor(self, *, media=None, identity=None):
        toc = SimpleNamespace(tracks=(1, 2, 3))
        return SimpleNamespace(
            device="/dev/sr0",
            identity="known-drive",
            loaded_toc=toc,
            path_exists=lambda dev: True,
            identity_reader=identity or (lambda dev: "known-drive"),
            media=media or (lambda dev: CDS_DISC_OK),
            read_toc=lambda dev: toc,
        )

    def make_clock(self):
        now = [0.0]

        def clock():
            return now[0]

        def sleep(seconds):
            now[0] += seconds

        return now, clock, sleep

    def test_ready_disc_immediately_succeeds(self):
        _, clock, sleep = self.make_clock()
        self.assertTrue(wait_for_woken_disc(
            self.make_monitor(), "/dev/sr0",
            clock=clock, sleep=sleep
        ))

    def test_delayed_spinup_becomes_ready(self):
        _, clock, sleep = self.make_clock()
        attempts = [0]

        def media(dev):
            attempts[0] += 1
            return (
                CDS_DRIVE_NOT_READY
                if attempts[0] < 4
                else CDS_DISC_OK
            )

        self.assertTrue(wait_for_woken_disc(
            self.make_monitor(media=media), "/dev/sr0",
            clock=clock, sleep=sleep
        ))
        self.assertEqual(attempts[0], 4)

    def test_timeout_is_bounded(self):
        now, clock, sleep = self.make_clock()

        result = wait_for_woken_disc(
            self.make_monitor(
                media=lambda dev: CDS_DRIVE_NOT_READY
            ),
            "/dev/sr0",
            timeout_seconds=2,
            poll_seconds=0.5,
            clock=clock,
            sleep=sleep,
        )

        self.assertFalse(result)
        self.assertEqual(now[0], 2.0)

    def test_wrong_identity_aborts_without_media_access(self):
        calls = []

        def media(dev):
            calls.append(dev)
            return CDS_DISC_OK

        _, clock, sleep = self.make_clock()
        monitor = self.make_monitor(
            media=media,
            identity=lambda dev: "different-drive",
        )

        self.assertFalse(wait_for_woken_disc(
            monitor, "/dev/sr0",
            clock=clock, sleep=sleep
        ))
        self.assertEqual(calls, [])

    def test_missing_device_aborts(self):
        _, clock, sleep = self.make_clock()
        monitor = self.make_monitor()
        monitor.path_exists = lambda dev: False

        self.assertFalse(wait_for_woken_disc(
            monitor, "/dev/sr0",
            clock=clock, sleep=sleep
        ))

    def test_changed_toc_is_handed_to_the_monitor(self):
        _, clock, sleep = self.make_clock()
        monitor = self.make_monitor()
        monitor.read_toc = lambda dev: SimpleNamespace(
            tracks=(9, 10)
        )

        self.assertTrue(wait_for_woken_disc(
            monitor, "/dev/sr0",
            timeout_seconds=1,
            clock=clock,
            sleep=sleep,
        ))


class USBWakeRaceTests(unittest.TestCase):

    def make_monitor(self, identity_reader, read_toc):
        toc = SimpleNamespace(tracks=(1, 2, 3))
        return SimpleNamespace(
            device="/dev/sr0",
            identity="original-usb",
            loaded_toc=toc,
            path_exists=lambda dev: True,
            identity_reader=identity_reader,
            media=lambda dev: CDS_DISC_OK,
            read_toc=read_toc,
        )

    def test_reconnect_during_toc_read_is_rejected(self):
        current = ["original-usb"]
        toc = SimpleNamespace(tracks=(1, 2, 3))

        def read_toc(device):
            current[0] = "replacement-usb"
            return toc

        monitor = self.make_monitor(
            lambda dev: current[0], read_toc
        )

        self.assertFalse(
            verify_woken_disc(monitor, "/dev/sr0")
        )

    def test_device_disappearing_during_toc_is_rejected(self):
        exists = [True]
        toc = SimpleNamespace(tracks=(1, 2, 3))
        monitor = self.make_monitor(
            lambda dev: "original-usb",
            lambda dev: toc,
        )

        def read_toc(device):
            exists[0] = False
            return toc

        monitor.read_toc = read_toc
        monitor.path_exists = lambda dev: exists[0]

        self.assertFalse(
            verify_woken_disc(monitor, "/dev/sr0")
        )

    def test_stable_identity_and_original_disc_succeed(self):
        toc = SimpleNamespace(tracks=(1, 2, 3))
        monitor = self.make_monitor(
            lambda dev: "original-usb",
            lambda dev: toc,
        )

        self.assertTrue(
            verify_woken_disc(monitor, "/dev/sr0")
        )

    def test_changed_disc_in_the_same_drive_is_handed_over(self):
        monitor = self.make_monitor(
            lambda dev: "original-usb",
            lambda dev: SimpleNamespace(tracks=(9, 10)),
        )

        self.assertTrue(
            wait_for_woken_disc(
                monitor, "/dev/sr0",
                timeout_seconds=0.5,
                poll_seconds=0.5,
                clock=self.clock(),
                sleep=lambda _: None,
            )
        )

    @staticmethod
    def clock():
        # Deterministic bounded wait without hardware.
        ticks = iter((0.0, 0.5, 0.5))
        return lambda: next(ticks)


class WakeDiagnosticTests(unittest.TestCase):

    def make_monitor(self, **overrides):
        toc = SimpleNamespace(tracks=(1, 2, 3))
        values = dict(
            device="/dev/sr0",
            identity="original-drive",
            loaded_toc=toc,
            path_exists=lambda device: True,
            identity_reader=lambda device: "original-drive",
            media=lambda device: CDS_DISC_OK,
            read_toc=lambda device: toc,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def check(self, monitor, expected_stage, expected_ok):
        diagnostics = {}
        result = verify_woken_disc(
            monitor, "/dev/sr0", diagnostics=diagnostics
        )
        self.assertIs(result, expected_ok)
        self.assertEqual(diagnostics["stage"], expected_stage)
        return diagnostics

    def test_verified_original_cd(self):
        details = self.check(
            self.make_monitor(), "verified", True
        )
        self.assertEqual(details["observed_tracks"], 3)

    def test_media_not_ready(self):
        self.check(
            self.make_monitor(
                media=lambda device: CDS_DRIVE_NOT_READY
            ),
            "media_not_ready", False,
        )

    def test_usb_identity_changed(self):
        self.check(
            self.make_monitor(
                identity_reader=lambda device: "replacement"
            ),
            "usb_identity_changed", False,
        )

    def test_toc_changed(self):
        details = self.check(
            self.make_monitor(
                read_toc=lambda device:
                    SimpleNamespace(tracks=(1, 2))
            ),
            "disc_changed", True,
        )
        self.assertEqual(details["expected_tracks"], 3)
        self.assertEqual(details["observed_tracks"], 2)

    def test_toc_exception(self):
        def fail(device):
            raise ValueError("simulated unreadable TOC")

        details = self.check(
            self.make_monitor(read_toc=fail),
            "check_exception", False,
        )
        self.assertEqual(details["check"], "toc_read")
        self.assertEqual(details["exception"], "ValueError")

    def test_diagnostics_do_not_repeat_media_or_toc_queries(self):
        calls = {"media": 0, "toc": 0}
        toc = SimpleNamespace(tracks=(1, 2, 3))

        def media(device):
            calls["media"] += 1
            return CDS_DISC_OK

        def read_toc(device):
            calls["toc"] += 1
            return toc

        monitor = self.make_monitor(
            media=media, read_toc=read_toc
        )
        self.assertTrue(
            verify_woken_disc(monitor, "/dev/sr0")
        )
        self.assertEqual(calls, {"media": 1, "toc": 1})

        calls = {"media": 0, "toc": 0}
        self.assertTrue(
            verify_woken_disc(
                monitor, "/dev/sr0", diagnostics={}
            )
        )
        self.assertEqual(calls, {"media": 1, "toc": 1})


def failed(cooldown=60.0, maximum=3, failures=1, at=100.0):
    p = IdleDrivePolicy(idle_seconds=300, spun_down=True, stop_attempted=True,
                        retry_cooldown_seconds=cooldown, max_wake_failures=maximum)
    p.wake_fault_latched = True
    p.wake_failures = failures
    p.fault_at = at
    return p


class PlayStaysBlockedTests(unittest.TestCase):
    def test_unconfirmed_stop_still_blocks_play(self):
        power = SimpleNamespace(idle_policy=IdleDrivePolicy(idle_seconds=300, stop_attempted=True),
                                activity=lambda: None)
        for command in ('play', 'play-selected', 'playpause'):
            self.assertFalse(player_service.wake_before_new_play(
                power, object(), {'command': command}, stopped=True))


class PolicyTests(unittest.TestCase):
    def test_failure_records_count_and_time(self):
        p = IdleDrivePolicy(idle_seconds=300, spun_down=True, wake_in_progress=True)
        self.assertFalse(p.wake_result(50.0, drive_verified=False))
        self.assertEqual((p.wake_failures, p.fault_at, p.wake_fault_latched), (1, 50.0, True))

    def test_retry_needs_the_full_cooldown(self):
        p = failed()
        self.assertFalse(p.wake_retry_due(159.9))
        self.assertTrue(p.wake_retry_due(160.0))

    def test_activity_inside_cooldown_keeps_the_latch(self):
        p = failed()
        self.assertIsNone(p.activity(130.0))
        self.assertTrue(p.wake_fault_latched)
        self.assertTrue(p.play_must_wait)

    def test_activity_after_cooldown_starts_exactly_one_wake(self):
        p = failed()
        self.assertEqual(p.activity(200.0), 'wake')
        self.assertFalse(p.wake_fault_latched)
        self.assertTrue(p.wake_in_progress)
        self.assertIsNone(p.activity(201.0))      # a second press does not start another

    def test_no_retry_after_the_last_allowed_failure(self):
        p = failed(failures=3)
        self.assertIsNone(p.activity(10_000.0))
        self.assertTrue(p.wake_fault_latched)

    def test_latch_without_a_confirmed_stop_is_never_lifted(self):
        p = failed()
        p.spun_down = False                        # inconsistent state: fail closed
        self.assertIsNone(p.activity(10_000.0))
        self.assertTrue(p.wake_fault_latched)

    def test_clock_going_backwards_never_retries(self):
        self.assertFalse(failed(at=500.0).wake_retry_due(100.0))

    def test_retry_state_for_display(self):
        self.assertEqual(IdleDrivePolicy(idle_seconds=300).retry_state(0), (None, False))
        self.assertEqual(failed().retry_state(110.0), (50, False))
        self.assertEqual(failed().retry_state(110.2), (50, False))
        self.assertEqual(failed().retry_state(900.0), (0, False))
        self.assertEqual(failed(failures=3).retry_state(110.0), (None, True))

    def test_parameters_are_validated(self):
        for kwargs in ({'retry_cooldown_seconds': 4}, {'retry_cooldown_seconds': 3601},
                       {'retry_cooldown_seconds': '60'}, {'max_wake_failures': 0},
                       {'max_wake_failures': 11}, {'max_wake_failures': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                IdleDrivePolicy(idle_seconds=300, **kwargs)

    def test_defaults_are_one_minute_and_three_attempts(self):
        p = IdleDrivePolicy(idle_seconds=300)
        self.assertEqual((p.retry_cooldown_seconds, p.max_wake_failures), (60.0, 3))


class EjectRecoveryTests(unittest.TestCase):
    def power(self, latched, stop_attempted=False, spun_down=False):
        calls = []
        policy = SimpleNamespace(wake_fault_latched=latched, stop_attempted=stop_attempted,
                                 spun_down=spun_down)
        return SimpleNamespace(idle_policy=policy,
                               clear_fault_after_eject=lambda: calls.append('cleared')), calls

    def test_successful_eject_clears_a_latched_fault(self):
        power, calls = self.power(True)
        with patch.object(player_service, 'eject_verified_idle', return_value=True) as eject:
            self.assertTrue(player_service.eject_and_recover('c', 'm', power))
        eject.assert_called_once_with('c', 'm')
        self.assertEqual(calls, ['cleared'])

    def test_successful_eject_clears_an_unconfirmed_stop(self):
        power, calls = self.power(False, stop_attempted=True)
        with patch.object(player_service, 'eject_verified_idle', return_value=True):
            player_service.eject_and_recover('c', 'm', power)
        self.assertEqual(calls, ['cleared'])

    def test_confirmed_stop_is_not_cleared_by_eject(self):
        power, calls = self.power(False, stop_attempted=True, spun_down=True)
        with patch.object(player_service, 'eject_verified_idle', return_value=True):
            player_service.eject_and_recover('c', 'm', power)
        self.assertEqual(calls, [])

    def test_refused_eject_keeps_the_fault(self):
        power, calls = self.power(True)
        with patch.object(player_service, 'eject_verified_idle',
                          side_effect=RuntimeError('EJECT refused: drive identity mismatch')):
            with self.assertRaises(RuntimeError):
                player_service.eject_and_recover('c', 'm', power)
        self.assertEqual(calls, [])

    def test_normal_eject_does_not_touch_the_policy(self):
        power, calls = self.power(False)
        with patch.object(player_service, 'eject_verified_idle', return_value=True):
            player_service.eject_and_recover('c', 'm', power)
        self.assertEqual(calls, [])

    def test_without_a_power_manager_nothing_changes(self):
        with patch.object(player_service, 'eject_verified_idle', return_value=True):
            self.assertTrue(player_service.eject_and_recover('c', 'm', None))


def fault_state(**extra):
    return {'drive_power': {'idle_wake_fault_latched': True, **extra}}


class OledTests(unittest.TestCase):
    def frame(self, state=None):
        return bytes(drive_notice_frame(state).data)

    def test_four_messages_are_distinct(self):
        frames = {
            self.frame(fault_state()),
            self.frame(fault_state(idle_wake_retry_in_seconds=42)),
            self.frame(fault_state(idle_wake_retry_in_seconds=0)),
            self.frame(fault_state(idle_wake_retries_exhausted=True)),
        }
        self.assertEqual(len(frames), 4)

    def test_countdown_changes_with_the_seconds(self):
        self.assertNotEqual(self.frame(fault_state(idle_wake_retry_in_seconds=42)),
                            self.frame(fault_state(idle_wake_retry_in_seconds=41)))

    def test_all_status_paths_show_the_detailed_frame(self):
        state = {'player': {'mode': 'STOPPED'},
                 'drive_power': {'idle_wake_fault_latched': True,
                                 'idle_wake_retry_in_seconds': 17}}
        expected = self.frame(state)
        self.assertEqual(bytes(drive_notice_frame(state).data), expected)
        self.assertEqual(bytes(modern_status_frame(state).data), expected)
        self.assertEqual(bytes(selected_status_frame(state, ui_modern=True).data), expected)

    def test_malformed_details_fall_back_to_the_generic_message(self):
        generic = self.frame(fault_state())
        for bad in (-1, '5', 1.5, True, None):
            with self.subTest(bad=bad):
                self.assertEqual(self.frame(fault_state(idle_wake_retry_in_seconds=bad)), generic)
        self.assertEqual(drive_fault_details(None), (None, False))
        self.assertEqual(drive_fault_details({'drive_power': 'x'}), (None, False))
        self.assertEqual(drive_fault_details(fault_state(idle_wake_retries_exhausted='yes')),
                         (None, False))

    def test_power_menu_stays_visible_over_fault_and_wait_notices(self):
        sentinel = object()
        menu = Mock()
        menu.active = True
        menu.power_menu = True
        menu.frame.return_value = sentinel
        for state in (fault_state(), {'drive_power': {'idle_waking': True}}):
            self.assertIs(selected_status_frame(state, menu=menu), sentinel)

    def test_other_menus_still_yield_to_the_fault_notice(self):
        menu = Mock()
        menu.active = True
        menu.power_menu = False
        frame = selected_status_frame(fault_state(), menu=menu)
        self.assertEqual(bytes(frame.data), self.frame(fault_state()))
        menu.frame.assert_not_called()


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
            "sonoscdplayer.player.service.wait_for_woken_disc",
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
            "sonoscdplayer.player.service.wait_for_woken_disc",
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


class FakeController:
    def __init__(self):
        self.server = SimpleNamespace(reader_active=lambda: False)

    def status(self):
        return "STOPPED"

    def status_snapshot(self):
        return {"player": {"mode": "STOPPED"}}


class FakePower:
    def __init__(self, entered, release, *, succeed=True):
        self.entered = entered
        self.release = release
        self.succeed = succeed
        self.idle_policy = SimpleNamespace(
            play_must_wait=False,
            wake_in_progress=False,
            wake_fault_latched=False,
        )

    def activity(self):
        self.idle_policy.play_must_wait = True
        self.idle_policy.wake_in_progress = True

    def wake(self, *, verify):
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("test wake gate timed out")
        self.idle_policy.wake_in_progress = False
        if not self.succeed:
            self.idle_policy.wake_fault_latched = True
            return False
        self.idle_policy.play_must_wait = False
        return True

    def snapshot(self, *, reader_active):
        return {
            "idle_waking": self.idle_policy.wake_in_progress,
            "idle_wake_fault_latched":
                self.idle_policy.wake_fault_latched,
        }


class WakeSocketSnapshotTests(unittest.TestCase):

    def run_scenario(self, *, succeed):
        entered = threading.Event()
        release = threading.Event()
        stop = threading.Event()
        results = []
        errors = []

        power = FakePower(entered, release, succeed=succeed)
        controller = FakeController()
        publisher = StatusPublisher(controller, power)

        with tempfile.TemporaryDirectory(prefix="drive-socket-") as tmp:
            path = str(Path(tmp) / "control.sock")

            with socket.socket(
                socket.AF_UNIX, socket.SOCK_STREAM
            ) as server:
                server.bind(path)
                server.listen(4)
                server.settimeout(0.1)

                def serve_status():
                    while not stop.is_set():
                        try:
                            conn, _ = server.accept()
                        except socket.timeout:
                            continue
                        except OSError:
                            break

                        with conn:
                            conn.settimeout(2)
                            try:
                                request = conn.recv(1024)
                                if request == b'{"command":"status"}\n':
                                    payload = publisher.read()
                                else:
                                    payload = {
                                        "ok": False,
                                        "error": "unexpected request",
                                    }
                                conn.sendall(
                                    json.dumps(payload).encode() + b"\n"
                                )
                            except Exception as exc:
                                errors.append(exc)

                def run_wake():
                    try:
                        ok = process_drive_activity(
                            power,
                            object(),
                            publish_wake=lambda:
                                publisher.publish(controller, power),
                        )
                        results.append(ok)
                        publisher.publish(controller, power)
                    except Exception as exc:
                        errors.append(exc)

                server_thread = threading.Thread(
                    target=serve_status, daemon=True
                )
                worker = threading.Thread(
                    target=run_wake, daemon=True
                )
                server_thread.start()

                try:
                    initial = request_status(path)
                    self.assertFalse(drive_waking(initial))

                    worker.start()
                    self.assertTrue(
                        entered.wait(2),
                        "Wake did not reach the simulated blocking gate",
                    )

                    # The worker is still blocked, yet the socket
                    # must serve the already published wake snapshot.
                    self.assertTrue(worker.is_alive())
                    during = request_status(path)
                    self.assertTrue(drive_waking(during))
                    self.assertFalse(drive_fault(during))

                    release.set()
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(results, [succeed])

                    after = request_status(path)
                    self.assertFalse(drive_waking(after))
                    self.assertEqual(
                        drive_fault(after), not succeed
                    )
                    self.assertEqual(errors, [])
                finally:
                    release.set()
                    stop.set()
                    worker.join(3) if worker.ident else None
                    server_thread.join(3)

    def test_status_remains_readable_during_successful_wake(self):
        self.run_scenario(succeed=True)

    def test_status_exposes_latched_fault_after_failed_wake(self):
        self.run_scenario(succeed=False)


class Controller:
    def __init__(self):
        self.state = SimpleNamespace(mode=Mode.STOPPED)
        self.server = SimpleNamespace(
            reader_active=lambda: False,
            alive=lambda: False,
            device="/dev/fake-drive-concurrent",
        )
        self.disc_status = None
        self.owned_url = None
        self.player = None
        self._joined = False
        self._group_uncertain = False
        self.status_hook = None

    def status(self):
        return "STOPPED"

    def status_snapshot(self):
        return {"player": {"mode": "STOPPED"}}

    def refresh(self):
        return None


class Monitor:
    def poll(self, controller):
        return False


class Power:
    def __init__(self, entered, release, attempts):
        from sonoscdplayer.drive.idle_policy import IdleDrivePolicy

        self.idle_policy = IdleDrivePolicy(
            idle_seconds=300, last_activity=0,
            spun_down=True, stop_attempted=True,
        )
        self.entered = entered
        self.release = release
        self.attempts = attempts

    def activity(self):
        return self.idle_policy.activity(time.monotonic()) == "wake"

    def wake(self, *, verify):
        if self.idle_policy.wake_fault_latched:
            return False

        with self.attempts.get_lock():
            self.attempts.value += 1

        self.entered.set()

        if not self.release.wait(5):
            raise RuntimeError("test wake timeout")

        self.idle_policy.wake_result(
            time.monotonic(), drive_verified=False
        )
        return False

    def snapshot(self, *, reader_active):
        p = self.idle_policy
        return {
            "idle_waking": p.wake_in_progress,
            "idle_wake_fault_latched": p.wake_fault_latched,
            "idle_spun_down": p.spun_down,
            "idle_stop_attempted": p.stop_attempted,
        }

    def tick(self, **kwargs):
        return None


def run_service(path, entered, release, attempts):
    from sonoscdplayer.player.service import serve

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "transport or EJECT handler must not be called"
        )

    with patch("sonoscdplayer.player.service.handle_command",
               side_effect=forbidden), \
         patch("sonoscdplayer.player.service.eject_verified_idle",
               side_effect=forbidden):
        serve(
            Controller(),
            path,
            poll_seconds=0.05,
            power=Power(entered, release, attempts),
            disc_monitor=Monitor(),
        )


def rpc(path, command, sent=None):
    with socket.socket(
        socket.AF_UNIX, socket.SOCK_STREAM
    ) as conn:
        conn.settimeout(5)
        conn.connect(path)
        conn.sendall(
            (json.dumps({"command": command}) + "\n").encode()
        )
        if sent is not None:
            sent.set()

        data = bytearray()
        while b"\n" not in data:
            part = conn.recv(4096)
            if not part:
                raise RuntimeError("socket closed without reply")
            data.extend(part)

    return json.loads(data.split(b"\n", 1)[0])


class ConcurrentWakeTests(unittest.TestCase):

    def test_queued_play_and_activity_after_failed_wake(self):
        ctx = multiprocessing.get_context("fork")
        entered = ctx.Event()
        release = ctx.Event()
        attempts = ctx.Value("i", 0)

        with tempfile.TemporaryDirectory(
            prefix="drive-concurrent-"
        ) as directory:
            path = str(Path(directory) / "control.sock")
            process = ctx.Process(
                target=run_service,
                args=(path, entered, release, attempts),
                daemon=True,
            )
            process.start()

            threads = []
            replies = {}
            errors = {}

            def client(name, command, sent):
                try:
                    replies[name] = rpc(path, command, sent)
                except Exception as exc:
                    errors[name] = exc

            try:
                deadline = time.monotonic() + 4
                while True:
                    if process.exitcode is not None:
                        self.fail("fake service exited prematurely")
                    try:
                        request_status(path, timeout=0.2)
                        break
                    except (OSError, TimeoutError, ValueError):
                        if time.monotonic() >= deadline:
                            self.fail("fake service did not start")
                        time.sleep(0.02)

                first_sent = threading.Event()
                t = threading.Thread(
                    target=client,
                    args=("first", "drive-activity", first_sent),
                )
                threads.append(t)
                t.start()

                self.assertTrue(first_sent.wait(2))
                self.assertTrue(entered.wait(3))

                # Worker is blocked in the first attempted wake.
                self.assertTrue(t.is_alive())
                self.assertEqual(attempts.value, 1)

                queued = []
                for name, command in (
                    ("play", "play-selected"),
                    ("activity", "drive-activity"),
                ):
                    sent = threading.Event()
                    queued.append(sent)
                    t = threading.Thread(
                        target=client,
                        args=(name, command, sent),
                    )
                    threads.append(t)
                    t.start()

                for sent in queued:
                    self.assertTrue(sent.wait(2))

                # Cached status must remain responsive.
                state = request_status(path, timeout=1)
                self.assertTrue(
                    state["drive_power"]["idle_waking"]
                )

                release.set()

                for t in threads:
                    t.join(4)
                    self.assertFalse(t.is_alive())

                self.assertEqual(errors, {})
                self.assertEqual(
                    set(replies), {"first", "play", "activity"}
                )
                for name, reply in replies.items():
                    with self.subTest(command=name):
                        self.assertIs(reply["ok"], False)

                # Exactly one motor wake attempt, despite more commands.
                self.assertEqual(attempts.value, 1)

                final = request_status(path, timeout=1)
                self.assertFalse(
                    final["drive_power"]["idle_waking"]
                )
                self.assertTrue(
                    final["drive_power"]["idle_wake_fault_latched"]
                )
                self.assertIsNone(process.exitcode)

            finally:
                release.set()
                for t in threads:
                    if t.ident is not None:
                        t.join(3)
                if process.is_alive():
                    process.terminate()
                process.join(3)
                if process.is_alive():
                    process.kill()
                    process.join(3)


class VerifiedMonitor(Monitor):
    def validate_before_play(self, controller):
        return True


class SuccessfulPower:
    def __init__(self, entered, release, starts):
        self.entered = entered
        self.release = release
        self.starts = starts
        self.idle_policy = IdleDrivePolicy(
            idle_seconds=300,
            last_activity=0,
            spun_down=True,
            stop_attempted=True,
        )

    def activity(self):
        return self.idle_policy.activity(
            time.monotonic()
        ) == "wake"

    def wake(self, *, verify):
        with self.starts.get_lock():
            self.starts.value += 1

        self.entered.set()

        if not self.release.wait(5):
            raise RuntimeError("simulated wake timeout")

        self.idle_policy.wake_result(
            time.monotonic(),
            drive_verified=True,
        )
        return True

    def snapshot(self, *, reader_active):
        p = self.idle_policy
        return {
            "idle_waking": p.wake_in_progress,
            "idle_wake_fault_latched": p.wake_fault_latched,
            "idle_spun_down": p.spun_down,
            "idle_stop_attempted": p.stop_attempted,
        }

    def tick(self, **kwargs):
        return None


def run_verified_service(path, entered, release, starts, plays):
    from sonoscdplayer.player.service import serve

    controller = Controller()

    def fake_handle(ctrl, request, selection=None, eject=None):
        if request != {"command": "play-selected"}:
            raise AssertionError(
                "unexpected transport command"
            )

        p = power.idle_policy

        if (
            p.spun_down
            or p.wake_in_progress
            or p.wake_fault_latched
            or p.stop_attempted
        ):
            raise AssertionError(
                "PLAY reached handler before verified wake"
            )

        with plays.get_lock():
            plays.value += 1

        return response(ctrl, ok=True)

    def forbidden_eject(*args, **kwargs):
        raise AssertionError("EJECT must not run")

    power = SuccessfulPower(
        entered, release, starts
    )

    with patch(
        "sonoscdplayer.player.service.handle_command",
        side_effect=fake_handle,
    ), patch(
        "sonoscdplayer.player.service.eject_verified_idle",
        side_effect=forbidden_eject,
    ):
        serve(
            controller,
            path,
            poll_seconds=0.05,
            power=power,
            disc_monitor=VerifiedMonitor(),
        )


class SuccessfulConcurrentWakeTests(unittest.TestCase):

    def test_one_wake_before_queued_play_and_activity(self):
        ctx = multiprocessing.get_context("fork")

        entered = ctx.Event()
        release = ctx.Event()
        starts = ctx.Value("i", 0)
        plays = ctx.Value("i", 0)

        with tempfile.TemporaryDirectory(
            prefix="drive-wake-success-"
        ) as directory:

            path = str(
                Path(directory) / "control.sock"
            )

            process = ctx.Process(
                target=run_verified_service,
                args=(
                    path, entered, release,
                    starts, plays,
                ),
                daemon=True,
            )
            process.start()

            replies = {}
            errors = {}
            threads = []

            def client(name, command, sent):
                try:
                    replies[name] = rpc(
                        path, command, sent
                    )
                except Exception as exc:
                    errors[name] = exc

            def start_client(name, command):
                sent = threading.Event()

                thread = threading.Thread(
                    target=client,
                    args=(name, command, sent),
                    daemon=True,
                )
                threads.append(thread)
                thread.start()

                self.assertTrue(sent.wait(2))

            try:
                deadline = time.monotonic() + 4

                while True:
                    if process.exitcode is not None:
                        self.fail(
                            "simulated service exited"
                        )

                    try:
                        request_status(
                            path, timeout=0.2
                        )
                        break

                    except (
                        OSError, ValueError,
                        TimeoutError,
                    ):
                        if time.monotonic() >= deadline:
                            self.fail(
                                "service did not start"
                            )
                        time.sleep(0.02)

                # First event starts the simulated wake.
                start_client(
                    "first", "drive-activity"
                )

                self.assertTrue(
                    entered.wait(3)
                )
                self.assertEqual(starts.value, 1)

                # Queue both commands while wake is blocked.
                start_client(
                    "play", "play-selected"
                )
                start_client(
                    "second", "drive-activity"
                )

                status = request_status(
                    path, timeout=1
                )
                self.assertTrue(
                    status["drive_power"][
                        "idle_waking"
                    ]
                )

                self.assertEqual(plays.value, 0)
                self.assertEqual(starts.value, 1)

                # Wake verification succeeds.
                release.set()

                for thread in threads:
                    thread.join(4)
                    self.assertFalse(
                        thread.is_alive()
                    )

                self.assertEqual(errors, {})
                self.assertEqual(
                    set(replies),
                    {"first", "play", "second"},
                )

                for reply in replies.values():
                    self.assertIs(
                        reply["ok"], True, reply
                    )

                self.assertEqual(starts.value, 1)
                self.assertEqual(plays.value, 1)

                final = request_status(
                    path, timeout=1
                )["drive_power"]

                self.assertFalse(
                    final["idle_waking"]
                )
                self.assertFalse(
                    final["idle_spun_down"]
                )
                self.assertFalse(
                    final["idle_wake_fault_latched"]
                )
                self.assertFalse(
                    final["idle_stop_attempted"]
                )

                self.assertIsNone(
                    process.exitcode
                )

            finally:
                release.set()

                for thread in threads:
                    if thread.ident is not None:
                        thread.join(3)

                if process.is_alive():
                    process.terminate()

                process.join(3)

                if process.is_alive():
                    process.kill()
                    process.join(3)


class ServeFakeController:
    def __init__(self):
        self.state = SimpleNamespace(mode=Mode.STOPPED)
        self.server = SimpleNamespace(
            reader_active=lambda: False,
            alive=lambda: False,
            device="/dev/fake-drive",
        )
        self.disc_status = None
        self.owned_url = None
        self._joined = False
        self._group_uncertain = False
        self.status_hook = None

    def status(self):
        return "STOPPED"

    def status_snapshot(self):
        return {"player": {"mode": "STOPPED"}}

    def refresh(self):
        return None


class FakeMonitor:
    def poll(self, controller):
        return False


class ServeFakePower:
    def __init__(self, entered, release, succeed):
        self.entered = entered
        self.release = release
        self.succeed = succeed
        self.idle_policy = SimpleNamespace(
            play_must_wait=False,
            wake_in_progress=False,
            wake_fault_latched=False,
            stop_attempted=False,
            spun_down=False,
        )

    def activity(self):
        self.idle_policy.play_must_wait = True
        self.idle_policy.wake_in_progress = True

    def wake(self, *, verify):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("simulated wake timed out")

        self.idle_policy.wake_in_progress = False

        if not self.succeed:
            self.idle_policy.wake_fault_latched = True
            return False

        self.idle_policy.play_must_wait = False
        return True

    def snapshot(self, *, reader_active):
        return {
            "idle_waking": self.idle_policy.wake_in_progress,
            "idle_wake_fault_latched":
                self.idle_policy.wake_fault_latched,
        }

    def tick(self, **kwargs):
        return None


def run_fake_service(path, entered, release, succeed):
    # Fail immediately if an activity RPC accidentally takes the
    # ordinary transport or EJECT path.
    from unittest.mock import patch
    from sonoscdplayer.player.service import serve

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected transport/EJECT operation")

    with patch("sonoscdplayer.player.service.handle_command",
               side_effect=forbidden), \
         patch("sonoscdplayer.player.service.eject_verified_idle",
               side_effect=forbidden):
        serve(
            ServeFakeController(),
            path,
            poll_seconds=0.05,
            power=ServeFakePower(entered, release, succeed),
            disc_monitor=FakeMonitor(),
        )


def serve_rpc(path, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(path)
        conn.sendall(
            (json.dumps({"command": command}) + "\n").encode()
        )

        response = bytearray()
        while b"\n" not in response:
            chunk = conn.recv(4096)
            if not chunk:
                raise RuntimeError("socket closed before response")
            response.extend(chunk)

    return json.loads(response.split(b"\n", 1)[0])


class RealServeSocketTests(unittest.TestCase):
    def run_scenario(self, succeed):
        ctx = multiprocessing.get_context("fork")
        entered = ctx.Event()
        release = ctx.Event()
        results = []
        errors = []

        with tempfile.TemporaryDirectory(
            prefix="drive-real-serve-"
        ) as directory:
            path = str(Path(directory) / "control.sock")

            process = ctx.Process(
                target=run_fake_service,
                args=(path, entered, release, succeed),
                daemon=True,
            )
            process.start()

            client = None
            try:
                # Wait for the isolated service to become ready.
                deadline = time.monotonic() + 4
                while True:
                    if process.exitcode is not None:
                        self.fail(
                            f"fake service exited: {process.exitcode}"
                        )

                    try:
                        initial = request_status(path, timeout=0.3)
                        break
                    except (OSError, ValueError, TimeoutError):
                        if time.monotonic() >= deadline:
                            self.fail("fake service did not start")
                        time.sleep(0.02)

                self.assertFalse(drive_waking(initial))

                def send_activity():
                    try:
                        results.append(serve_rpc(path, "drive-activity"))
                    except Exception as exc:
                        errors.append(exc)

                client = threading.Thread(
                    target=send_activity,
                    daemon=True,
                )
                client.start()

                self.assertTrue(
                    entered.wait(3),
                    "drive-activity did not enter simulated wake",
                )

                self.assertTrue(client.is_alive())

                # Real serve() must answer from its cached status
                # while its one controller worker is blocked.
                during = request_status(path, timeout=1.0)
                self.assertTrue(drive_waking(during))
                self.assertFalse(drive_fault(during))

                release.set()
                client.join(3)

                self.assertFalse(client.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(results), 1)
                self.assertIs(results[0]["ok"], succeed)

                deadline = time.monotonic() + 2
                while True:
                    after = request_status(path, timeout=1.0)
                    if not drive_waking(after):
                        break
                    if time.monotonic() >= deadline:
                        self.fail("wake status never cleared")
                    time.sleep(0.02)

                self.assertEqual(
                    drive_fault(after), not succeed
                )

                # No real drive or Sonos operation is possible:
                # both are replaced with fake interfaces.
                self.assertIsNone(process.exitcode)

            finally:
                release.set()
                if client is not None and client.ident is not None:
                    client.join(3)

                if process.is_alive():
                    process.terminate()
                process.join(3)

                if process.is_alive():
                    process.kill()
                    process.join(3)

    def test_real_serve_successful_wake(self):
        self.run_scenario(True)

    def test_real_serve_failed_wake_latches_fault(self):
        self.run_scenario(False)


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

    @patch("sonoscdplayer.drive.power.drive_lock", fake_drive_lock)
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

    @patch("sonoscdplayer.drive.power.drive_lock", fake_drive_lock)
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

    @patch("sonoscdplayer.drive.power.drive_lock", fake_drive_lock)
    def test_failure_keeps_idle_stop_state(self):
        power, commands = self.make_power()

        self.assertFalse(power.wake(verify=lambda device: True))

        self.assertTrue(power.idle_policy.stop_attempted)
        self.assertTrue(power.idle_policy.spun_down)
        self.assertFalse(power.idle_policy.wake_in_progress)
        self.assertTrue(power.idle_policy.play_must_wait)
        self.assertEqual(len(commands), 1)


if __name__ == '__main__':
    unittest.main()
