"""M0.19 wake-fault recovery: bounded retry, EJECT escape hatch, OLED message."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import player_service
from drive_idle_policy import IdleDrivePolicy
from integrations.outputs.oled_status import (
    drive_fault_details, drive_fault_frame, drive_wait_frame, modern_status_frame,
    selected_status_frame, status_frame,
)


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
        return bytes(drive_fault_frame(state).data)

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
        self.assertEqual(bytes(status_frame(state).data), expected)
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


if __name__ == '__main__':
    unittest.main()
