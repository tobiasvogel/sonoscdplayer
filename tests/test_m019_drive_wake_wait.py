"""Bounded post-start disc readiness tests; no hardware access."""
import unittest
from types import SimpleNamespace

from disc_startup import CDS_DISC_OK, CDS_DRIVE_NOT_READY
from drive_wake_validation import wait_for_woken_disc


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

    def test_changed_toc_never_accepted(self):
        _, clock, sleep = self.make_clock()
        monitor = self.make_monitor()
        monitor.read_toc = lambda dev: SimpleNamespace(
            tracks=(9, 10)
        )

        self.assertFalse(wait_for_woken_disc(
            monitor, "/dev/sr0",
            timeout_seconds=1,
            clock=clock,
            sleep=sleep,
        ))


if __name__ == "__main__":
    unittest.main()
