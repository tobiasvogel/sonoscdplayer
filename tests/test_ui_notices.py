"""UI notices: model, localization, raster, timing policies, priorities and lifecycle."""
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from sonos_ui.notice import Notice, notice_text
from sonos_ui.notice_render import NoticeRenderer, icon_file
from sonos_ui.notice_policy import NoticeTiming
from sonos_ui.notice_engine import NoticeEngine



class NoticeModelTests(unittest.TestCase):
    def test_immutable_parameters_are_copied(self):
        params = {"seconds": 5}
        item = Notice("network.reset_warning", severity="warning",
                      params=params, priority=80, dismissible=False)
        params["seconds"] = 2
        self.assertEqual(item.params["seconds"], 5)
        with self.assertRaises(TypeError):
            item.params["seconds"] = 3
        with self.assertRaises(FrozenInstanceError):
            item.priority = 1

    def test_reject_invalid_models(self):
        for kwargs in ({"key": ""}, {"key": "x", "severity": "fatal"},
                       {"key": "x", "duration_seconds": -1},
                       {"key": "x", "duration_seconds": float("nan")},
                       {"key": "x", "params": []},
                       {"key": "x", "priority": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Notice(**kwargs)

    def test_localization_and_fallback(self):
        n = Notice("network.reset_warning", "warning", {"seconds": 5})
        self.assertIn("5", notice_text(n, "de"))
        self.assertIn("5", notice_text(n, "en"))
        self.assertEqual(notice_text(n, "xx"), notice_text(n, "en"))
        with self.assertRaises(KeyError):
            notice_text(Notice("not.a.real.key"))

    def test_parameter_error_is_visible_to_caller(self):
        with self.assertRaises(KeyError):
            notice_text(Notice("network.reset_warning"))

    def test_raster_sizes_and_palette(self):
        renderer = NoticeRenderer()
        item = Notice("system.please_wait")
        for size in ((64, 128), (128, 128), (200, 200)):
            with self.subTest(size=size):
                rgb = renderer.render(item, size=size)
                gray = renderer.render(item, size=size, grayscale=True)
                self.assertEqual((rgb.mode, rgb.size), ("RGB", size))
                self.assertEqual((gray.mode, gray.size), ("L", size))
                self.assertTrue(set(gray.getdata()).issubset(set(range(0, 256, 17))))

    def test_no_icons_still_renders(self):
        with TemporaryDirectory() as directory:
            renderer = NoticeRenderer(icon_root=directory)
            self.assertEqual(renderer.render(Notice("system.restarting")).size,
                             (128, 128))
            self.assertIsNone(icon_file("info", icon_root=directory))

    def test_only_normalized_info_icon_names_are_accepted(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix, grayscale in (("bw_small", True),
                                      ("color_small", False)):
                old = root / ("info_ " + suffix + ".png")
                modern = root / ("info_" + suffix + ".png")
                old.write_bytes(b"legacy")
                self.assertIsNone(icon_file("info", grayscale, root))
                modern.write_bytes(b"normalized")
                self.assertEqual(icon_file("info", grayscale, root), modern)
                modern.unlink()
                self.assertIsNone(icon_file("info", grayscale, root))

    def test_normalized_icons_are_actually_rendered_not_fallback(self):
        from PIL import Image
        with TemporaryDirectory() as directory:
            root = Path(directory)
            # A marker artwork must visibly change a notice raster compared
            # with the no-icon fallback, for both pixel formats.
            for grayscale in (False, True):
                suffix = "bw_small" if grayscale else "color_small"
                Image.new("RGBA", (16, 16), (255, 255, 255, 255)).save(
                    root / ("info_" + suffix + ".png"))
                notice = Notice("system.please_wait", severity="info")
                actual = NoticeRenderer(icon_root=root).render(
                    notice, grayscale=grayscale)
                fallback = NoticeRenderer(icon_root=root / "missing").render(
                    notice, grayscale=grayscale)
                self.assertNotEqual(actual.tobytes(), fallback.tobytes())

    def test_published_icons_are_resolvable(self):
        root = Path(__file__).resolve().parents[1] / "src" / "data" / "icons"
        for severity in ("info", "success", "warning", "error"):
            if root.is_dir():
                with self.subTest(severity=severity):
                    self.assertIsNotNone(icon_file(severity, icon_root=root))


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


if __name__ == '__main__':
    unittest.main()
