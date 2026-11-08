"""Offline UI-NOTICE-02 priority and lifecycle regression tests."""
import unittest

from sonos_ui.notice import Notice
from sonos_ui.notice_engine import NoticeEngine
from sonos_ui.notice_policy import NoticeTiming


class NoticeEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = NoticeEngine()
        self.info = Notice("system.ready", priority=10)

    def shown(self, now, view="playback"):
        return self.engine.presentation(now=now, base_view=view)

    def test_standard_timeout_and_updated_view(self):
        self.engine.post(self.info, now=10)
        self.assertEqual(self.shown(14).notice, self.info)
        result = self.shown(15, view="menu")
        self.assertIsNone(result.notice)
        self.assertEqual(result.base_view, "menu")
        self.assertFalse(result.showing_notice)

    def test_zero_requires_encoder_confirmation(self):
        notice = Notice("network.status", duration_seconds=0)
        self.engine.post(notice, now=0)
        self.assertEqual(self.shown(100).notice, notice)
        self.assertTrue(self.engine.confirm(now=101))
        self.assertIsNone(self.shown(101).notice)

    def test_errors_never_auto_hide_and_can_be_acknowledged(self):
        error = Notice("system.error", severity="error", duration_seconds=1)
        self.engine.post(error, now=0)
        self.assertEqual(self.shown(100).notice, error)
        self.assertTrue(self.engine.confirm(now=100))
        self.assertIsNone(self.shown(101).notice)

    def test_nondismissible_requires_source_clear(self):
        notice = Notice("system.shutting_down", dismissible=False,
                        duration_seconds=0, priority=100)
        ident = self.engine.post(notice, now=0)
        self.assertFalse(self.engine.confirm(now=10))
        self.assertEqual(self.shown(20).notice, notice)
        self.assertTrue(self.engine.clear(ident, now=20))
        self.assertIsNone(self.shown(20).notice)

    def test_higher_priority_pauses_remaining_time(self):
        low = Notice("system.ready", priority=10, duration_seconds=5)
        high = Notice("network.reset_warning", "warning", {"seconds": 3},
                      priority=80, duration_seconds=2)
        self.engine.post(low, now=0)
        self.engine.post(high, now=2)
        self.assertEqual(self.shown(3).notice, high)
        self.assertEqual(self.shown(4).notice, low)
        self.assertEqual(self.shown(6).notice, low)
        self.assertIsNone(self.shown(7).notice)

    def test_waiting_notice_does_not_expire_offscreen(self):
        high = Notice("system.error", severity="error", priority=90)
        low = Notice("system.ready", priority=1, duration_seconds=5)
        self.engine.post(high, now=0)
        self.engine.post(low, now=1)
        self.assertEqual(self.shown(300).notice, high)
        self.assertTrue(self.engine.confirm(now=301))
        self.assertEqual(self.shown(305).notice, low)
        self.assertIsNone(self.shown(306).notice)

    def test_fifo_at_equal_priority(self):
        first = Notice("system.error", "error", priority=10)
        second = Notice("network.status", priority=10, duration_seconds=0)
        self.engine.post(first, now=0)
        self.engine.post(second, now=1)
        self.assertEqual(self.shown(2).notice, first)
        self.assertTrue(self.engine.confirm(now=2))
        self.assertEqual(self.shown(2).notice, second)

    def test_lower_priority_cannot_interrupt(self):
        first = Notice("system.ready", priority=90, duration_seconds=3)
        second = Notice("network.status", priority=10, duration_seconds=5)
        self.engine.post(first, now=0)
        self.engine.post(second, now=1)
        self.assertEqual(self.shown(2).notice, first)
        self.assertEqual(self.shown(3).notice, second)
        self.assertIsNone(self.shown(8).notice)

    def test_clearing_queued_notice(self):
        first = Notice("system.error", severity="error", priority=90)
        self.engine.post(first, now=0)
        queued_id = self.engine.post(self.info, now=1)
        self.assertTrue(self.engine.clear(queued_id, now=2))
        self.assertFalse(self.engine.clear(queued_id, now=2))
        self.assertEqual(self.engine.pending_count(now=2), 1)

    def test_timed_message_not_confirmable(self):
        self.engine.post(self.info, now=0)
        self.assertFalse(self.engine.confirm(now=1))
        self.assertEqual(self.shown(1).notice, self.info)

    def test_time_cannot_go_backwards_or_be_nan(self):
        self.shown(10)
        with self.assertRaises(ValueError):
            self.shown(9)
        with self.assertRaises(ValueError):
            self.shown(float("nan"))

    def test_sparse_ticks_expire_successive_messages(self):
        first = Notice("system.ready", priority=10, duration_seconds=2)
        second = Notice("network.status", priority=1, duration_seconds=3)
        self.engine.post(first, now=0)
        self.engine.post(second, now=1)
        self.assertIsNone(self.shown(10).notice)
        self.assertEqual(self.engine.pending_count(now=10), 0)

    def test_multiple_preemptions_preserve_remaining(self):
        low = Notice("system.ready", priority=1, duration_seconds=5)
        mid = Notice("network.status", priority=2, duration_seconds=3)
        high = Notice("system.error", severity="error", priority=3)
        self.engine.post(low, now=0)
        self.engine.post(mid, now=1)
        self.engine.post(high, now=2)
        self.assertTrue(self.engine.confirm(now=10))
        self.assertEqual(self.shown(11.5).notice, mid)
        self.assertEqual(self.shown(12).notice, low)
        self.assertEqual(self.shown(15).notice, low)
        self.assertIsNone(self.shown(16).notice)

    def test_configurable_default(self):
        engine = NoticeEngine(timing=NoticeTiming(default_duration_seconds=8))
        engine.post(self.info, now=0)
        self.assertIsNotNone(engine.presentation(now=7, base_view="idle").notice)
        self.assertIsNone(engine.presentation(now=8, base_view="idle").notice)


if __name__ == "__main__":
    unittest.main()
