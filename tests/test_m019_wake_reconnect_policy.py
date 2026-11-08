"""M0.19 reconnect policy tests. No hardware I/O."""

import unittest

from drive_identity import UsbDriveId
from wake_reconnect_policy import (
    RecoveryState,
    assess_reconnect,
)

ORIGINAL = UsbDriveId("152d", "0583", "test-serial")
OTHER = UsbDriveId("152d", "0583", "other-serial")


class ReconnectPolicyTests(unittest.TestCase):

    def assess(
        self,
        identities,
        *,
        disconnected=True,
        disc=True,
        original=ORIGINAL,
    ):
        return assess_reconnect(
            original_identity=original,
            observed_identities=identities,
            usb_disconnect_observed=disconnected,
            same_disc_verified=disc,
        )

    def test_original_device_and_disc_is_candidate(self):
        result = self.assess([ORIGINAL])
        self.assertEqual(
            result.state, RecoveryState.CANDIDATE
        )
        self.assertFalse(result.playback_authorized)

    def test_missing_device_blocked(self):
        self.assertEqual(
            self.assess([]).reason,
            "original_drive_missing",
        )

    def test_different_device_blocked(self):
        self.assertEqual(
            self.assess([OTHER]).reason,
            "original_drive_missing",
        )

    def test_ambiguous_device_blocked(self):
        self.assertEqual(
            self.assess([ORIGINAL, ORIGINAL]).reason,
            "original_drive_ambiguous",
        )

    def test_unverified_disc_blocked(self):
        self.assertEqual(
            self.assess([ORIGINAL], disc=False).reason,
            "original_disc_unverified",
        )

    def test_missing_disconnect_evidence_blocked(self):
        self.assertEqual(
            self.assess([ORIGINAL], disconnected=False).reason,
            "no_disconnect_observed",
        )

    def test_invalid_identity_blocked(self):
        self.assertEqual(
            self.assess([None]).reason,
            "observations_invalid",
        )

    def test_missing_original_identity_blocked(self):
        self.assertEqual(
            self.assess([ORIGINAL], original=None).reason,
            "original_identity_unavailable",
        )

    def test_no_result_authorizes_playback(self):
        cases = (
            self.assess([ORIGINAL]),
            self.assess([]),
            self.assess([OTHER]),
            self.assess([ORIGINAL], disc=False),
        )
        for result in cases:
            self.assertFalse(result.playback_authorized)


if __name__ == "__main__":
    unittest.main()
