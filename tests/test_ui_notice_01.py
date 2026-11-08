"""Offline-only UI notice model, localization and raster regressions."""
import unittest
from dataclasses import FrozenInstanceError
from tempfile import TemporaryDirectory
from pathlib import Path
from sonos_ui.notice import Notice, notice_text
from sonos_ui.notice_render import NoticeRenderer, icon_file


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

    def test_published_icons_are_resolvable(self):
        root = Path(__file__).resolve().parents[1] / "src" / "data" / "icons"
        for severity in ("info", "success", "warning", "error"):
            if root.is_dir():
                with self.subTest(severity=severity):
                    self.assertIsNotNone(icon_file(severity, icon_root=root))


if __name__ == "__main__":
    unittest.main()
