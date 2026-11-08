"""Offline-only notice timing policies (no timers, GPIO or Sonos)."""
import unittest
from sonos_ui.notice import Notice
from sonos_ui.notice_policy import NoticeTiming


class NoticeTimingTests(unittest.TestCase):
    def test_default_is_five_seconds(self):
        policy = NoticeTiming()
        self.assertEqual(policy.automatic_dismiss_after(Notice("system.ready")), 5)
        self.assertFalse(policy.encoder_can_confirm(Notice("system.ready")))

    def test_configurable_duration(self):
        policy = NoticeTiming(default_duration_seconds=8)
        self.assertEqual(policy.automatic_dismiss_after(Notice("system.ready")), 8)
        self.assertEqual(policy.automatic_dismiss_after(
            Notice("system.ready", duration_seconds=2)), 2)

    def test_zero_requires_confirmation(self):
        policy = NoticeTiming(default_duration_seconds=0)
        notice = Notice("network.status")
        self.assertIsNone(policy.automatic_dismiss_after(notice))
        self.assertTrue(policy.encoder_can_confirm(notice))
        override = Notice("network.status", duration_seconds=0)
        self.assertIsNone(NoticeTiming().automatic_dismiss_after(override))
        self.assertTrue(NoticeTiming().encoder_can_confirm(override))

    def test_errors_never_auto_hide(self):
        for seconds in (None, 0, 1, 20):
            with self.subTest(seconds=seconds):
                notice = Notice("system.error", severity="error",
                                duration_seconds=seconds)
                self.assertIsNone(NoticeTiming().automatic_dismiss_after(notice))
                self.assertTrue(NoticeTiming().encoder_can_confirm(notice))

    def test_nondismissible_waits_for_owning_state(self):
        notice = Notice("system.shutting_down", dismissible=False,
                        duration_seconds=None)
        policy = NoticeTiming()
        self.assertIsNone(policy.automatic_dismiss_after(notice))
        self.assertFalse(policy.encoder_can_confirm(notice))

    def test_bad_defaults_rejected(self):
        for invalid in (-1, float("nan"), float("inf"), True, "5"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                NoticeTiming(invalid)


if __name__ == "__main__":
    unittest.main()
