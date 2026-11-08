"""Offline M0.19 idle policy checks; no USB, CD, GPIO, Sonos or OLED."""
import unittest

from drive_idle_policy import IdleDrivePolicy


class IdleDrivePolicyTests(unittest.TestCase):
    def test_idle_deadline_and_one_stop_per_idle_epoch(self):
        p = IdleDrivePolicy(last_activity=10)
        self.assertIsNone(p.idle_action(309, playback_active=False, reader_active=False))
        self.assertEqual(p.idle_action(310, playback_active=False, reader_active=False), 'stop')
        self.assertIsNone(p.idle_action(900, playback_active=False, reader_active=False))

    def test_active_playback_and_reader_never_stop(self):
        for playing, reading in ((True, False), (False, True), (True, True)):
            with self.subTest(playing=playing, reading=reading):
                p = IdleDrivePolicy(last_activity=0)
                self.assertIsNone(p.idle_action(600, playback_active=playing, reader_active=reading))
                self.assertIsNone(p.idle_action(601, playback_active=False, reader_active=False))

    def test_unknown_drive_or_cleanup_blocks_stop(self):
        p = IdleDrivePolicy()
        self.assertIsNone(p.idle_action(600, playback_active=False, reader_active=False, drive_ready=False))
        self.assertIsNone(p.idle_action(600, playback_active=False, reader_active=False, group_cleanup_pending=True))

    def test_stop_failure_does_not_claim_asleep_or_immediately_retry(self):
        p = IdleDrivePolicy()
        self.assertEqual(p.idle_action(301, playback_active=False, reader_active=False), 'stop')
        p.stop_result(acknowledged=False)
        self.assertFalse(p.spun_down)
        self.assertIsNone(p.idle_action(602, playback_active=False, reader_active=False))
        p.activity(603)
        # An unconfirmed STOP is not cleared by another button press.
        # No automatic motor retry is permitted, even after a new deadline.
        self.assertTrue(p.stop_attempted)
        self.assertFalse(p.spun_down)
        self.assertFalse(p.keepalive_allowed(604))
        self.assertIsNone(p.idle_action(902, playback_active=False, reader_active=False))
        self.assertIsNone(p.idle_action(903, playback_active=False, reader_active=False))
        self.assertTrue(p.stop_attempted)

    def test_keepalive_continues_every_interval_before_idle_deadline(self):
        p = IdleDrivePolicy(idle_seconds=300, last_activity=0)
        # Simulate existing 10-second cadence. Keepalive must never
        # extend the inactivity window by resetting last_activity.
        for seconds in range(10, 300, 10):
            with self.subTest(seconds=seconds):
                self.assertTrue(p.keepalive_allowed(seconds))
                self.assertIsNone(p.idle_action(
                    seconds, playback_active=False, reader_active=False))
        self.assertEqual(p.last_activity, 0)
        self.assertFalse(p.keepalive_allowed(300))
        self.assertEqual(p.idle_action(
            300, playback_active=False, reader_active=False), 'stop')
        self.assertFalse(p.keepalive_allowed(301))
        p.stop_result(acknowledged=True)
        self.assertFalse(p.keepalive_allowed(350))

    def test_new_user_activity_restarts_both_idle_and_keepalive_window(self):
        p = IdleDrivePolicy(idle_seconds=300, last_activity=0)
        self.assertTrue(p.keepalive_allowed(290))
        self.assertIsNone(p.activity(295))
        self.assertTrue(p.keepalive_allowed(590))
        self.assertFalse(p.keepalive_allowed(595))
        self.assertEqual(p.idle_action(
            595, playback_active=False, reader_active=False), 'stop')

    def test_uncertain_stop_disables_keepalive_without_blind_retry(self):
        p = IdleDrivePolicy(idle_seconds=300)
        self.assertEqual(p.idle_action(
            300, playback_active=False, reader_active=False), 'stop')
        p.stop_result(acknowledged=False)
        self.assertFalse(p.keepalive_allowed(301))
        self.assertIsNone(p.idle_action(
            900, playback_active=False, reader_active=False))

    def test_button_and_encoder_reset_idle_timer(self):
        p = IdleDrivePolicy()
        self.assertIsNone(p.activity(299))
        self.assertIsNone(p.idle_action(300, playback_active=False, reader_active=False))
        self.assertEqual(p.idle_action(599, playback_active=False, reader_active=False), 'stop')

    def test_first_play_after_stop_requires_wake_then_verified_drive(self):
        p = IdleDrivePolicy()
        self.assertEqual(p.idle_action(301, playback_active=False, reader_active=False), 'stop')
        p.stop_result(acknowledged=True)
        self.assertTrue(p.play_must_wait)
        self.assertEqual(p.activity(320), 'wake')
        self.assertIsNone(p.activity(321))
        self.assertTrue(p.play_must_wait)
        self.assertFalse(p.wake_result(325, drive_verified=False))
        self.assertTrue(p.play_must_wait)
        self.assertTrue(p.wake_fault_latched)
        self.assertIsNone(p.activity(330))  # No implicit retry after failure.
        self.assertTrue(p.play_must_wait)
        self.assertFalse(p.keepalive_allowed(331))
        self.assertIsNone(p.idle_action(999, playback_active=False,
                                        reader_active=False))

    def test_stop_while_waiting_to_wake_is_forbidden(self):
        p = IdleDrivePolicy()
        p.stop_result(acknowledged=True)
        self.assertEqual(p.activity(400), 'wake')
        self.assertIsNone(p.idle_action(900, playback_active=False, reader_active=False))
        self.assertTrue(p.play_must_wait)

    def test_config_bounds(self):
        for value in (False, 29, 86401, '300', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                IdleDrivePolicy(idle_seconds=value)
        self.assertEqual(IdleDrivePolicy(idle_seconds=300).idle_seconds, 300)


if __name__ == '__main__':
    unittest.main()
