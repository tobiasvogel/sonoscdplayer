"""Passive wake diagnostic codes; no motor, USB or Sonos I/O."""
import unittest
from types import SimpleNamespace

from disc_startup import CDS_DISC_OK, CDS_DRIVE_NOT_READY
from drive_wake_validation import verify_woken_disc


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
            "toc_changed", False,
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


if __name__ == "__main__":
    unittest.main()
