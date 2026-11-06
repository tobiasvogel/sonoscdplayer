"""OPS-01 maintenance gate: missing/stale/unproven evidence fails closed."""
import copy
import unittest

from tools.config_maintenance_gate import assess_maintenance_evidence

OLD = "a" * 64
NEW = "b" * 64
GOOD = {
    "operator_approved": True,
    "player_service_inactive": True,
    "player_process_absent": True,
    "control_socket_listener_absent": True,
    "reader_process_absent": True,
    "group_recovery_resolved": True,
    "other_config_writers_excluded": True,
    "private_directory_verified": True,
    "exclusive_writer_lock_held": True,
    "original_sha256": OLD,
    "candidate_sha256": NEW,
    "maintenance_window_id": "ops01-test-window",
}


class MaintenanceEvidenceTests(unittest.TestCase):
    def assess(self, payload, old=OLD, new=NEW):
        return assess_maintenance_evidence(
            payload, expected_original_sha256=old,
            expected_candidate_sha256=new)

    def test_complete_claimed_evidence_never_authorizes_writes(self):
        result = self.assess(GOOD)
        self.assertEqual(result["assessment"], "claimed_conditions_consistent")
        self.assertFalse(result["write_authorized"])
        self.assertTrue(result["live_verification_required"])
        self.assertTrue(result["noncooperating_writer_exclusion_unproven"])

    def test_missing_or_unknown_evidence_is_blocked(self):
        for payload in (None, {}, "ready", []):
            with self.subTest(payload=payload):
                self.assertEqual(self.assess(payload)["assessment"], "blocked")

    def test_each_missing_claim_blocks(self):
        for field in list(GOOD)[:9]:
            with self.subTest(field=field):
                evidence = dict(GOOD)
                del evidence[field]
                self.assertEqual(self.assess(evidence)["assessment"], "blocked")

    def test_each_false_or_ambiguous_claim_blocks(self):
        for field in list(GOOD)[:9]:
            for value in (False, None, 1, "true"):
                with self.subTest(field=field, value=value):
                    evidence = dict(GOOD)
                    evidence[field] = value
                    self.assertEqual(self.assess(evidence)["assessment"], "blocked")

    def test_stale_or_malformed_hashes_block(self):
        for field in ("original_sha256", "candidate_sha256"):
            with self.subTest(field=field):
                evidence = dict(GOOD)
                evidence[field] = "c" * 64
                self.assertEqual(self.assess(evidence)["assessment"], "blocked")
        self.assertEqual(self.assess(GOOD, old="bad")["assessment"], "blocked")
        self.assertEqual(self.assess(GOOD, new="BAD")["assessment"], "blocked")

    def test_maintenance_window_required(self):
        for value in ("", None, "contains spaces", "short"):
            with self.subTest(value=value):
                evidence = dict(GOOD)
                evidence["maintenance_window_id"] = value
                self.assertEqual(self.assess(evidence)["assessment"], "blocked")

    def test_no_secret_or_identifier_values_echoed(self):
        evidence = copy.deepcopy(GOOD)
        evidence["maintenance_window_id"] = "PRIVATE_LOCATION_OR_ID!"
        evidence["private_uid"] = "SECRET_UID"
        output = repr(self.assess(evidence))
        self.assertNotIn("PRIVATE_LOCATION_OR_ID", output)
        self.assertNotIn("SECRET_UID", output)
        self.assertFalse(self.assess(evidence)["write_authorized"])


if __name__ == "__main__":
    unittest.main()
