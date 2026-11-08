"""USB identity changes during wake verification; offline only."""
import unittest
from types import SimpleNamespace

from disc_startup import CDS_DISC_OK
from drive_wake_validation import (
    verify_woken_disc,
    wait_for_woken_disc,
)


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

    def test_changed_disc_is_never_accepted(self):
        monitor = self.make_monitor(
            lambda dev: "original-usb",
            lambda dev: SimpleNamespace(tracks=(9, 10)),
        )

        self.assertFalse(
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


if __name__ == '__main__':
    unittest.main()
