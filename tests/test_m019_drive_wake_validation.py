"""Wake verification tests; all device operations are simulated."""
import unittest
from types import SimpleNamespace
from disc_startup import CDS_DISC_OK, CDS_NO_DISC
from drive_wake_validation import verify_woken_disc


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

    def test_different_toc_rejected(self):
        monitor = self.make_monitor(
            read_toc=lambda device: SimpleNamespace(
                tracks=(1, 2, 3, 4)
            )
        )
        self.assertFalse(verify_woken_disc(monitor, "/dev/sr0"))

    def test_unreadable_disc_rejected(self):
        def fail(device):
            raise OSError("disc unreadable")

        monitor = self.make_monitor(read_toc=fail)
        self.assertFalse(verify_woken_disc(monitor, "/dev/sr0"))

    def test_unknown_original_toc_rejected(self):
        monitor = self.make_monitor(loaded_toc=None)
        self.assertFalse(verify_woken_disc(monitor, "/dev/sr0"))


if __name__ == "__main__":
    unittest.main()
