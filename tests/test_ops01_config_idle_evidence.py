"""OPS-01: an idle-looking status is never configuration-write permission."""
import copy
import unittest

from tools.config_idle_evidence import assess_idle_evidence


GOOD = {
    "ok": True,
    "state": {
        "schema_version": 1,
        "player": {"mode": "STOPPED", "track": 1, "track_count": 21},
        "sonos": {"owned": False, "group_cleanup_pending": False,
                  "transport": "STOPPED"},
        "stream": {"active": False},
    },
}
INTERNAL = {
    "reader_active": False,
    "server_alive": False,
    "group_joined": False,
    "group_uncertain": False,
    "operation_in_progress": False,
}


class IdleEvidenceTests(unittest.TestCase):
    def test_cached_status_alone_is_insufficient(self):
        report = assess_idle_evidence(GOOD)
        self.assertEqual(report["assessment"], "blocked")
        self.assertFalse(report["write_authorized"])
        self.assertIn("missing_controller_level_evidence", report["reasons"])

    def test_consistent_evidence_still_requires_live_exclusive_checks(self):
        report = assess_idle_evidence(GOOD, INTERNAL)
        self.assertEqual(report["assessment"], "incomplete_live_verification")
        self.assertTrue(report["evidence_consistent_with_idle"])
        self.assertFalse(report["write_authorized"])
        self.assertTrue(report["requires_live_exclusive_preflight"])

    def test_active_player_and_owned_stream_are_blocked(self):
        status = copy.deepcopy(GOOD)
        status["state"]["player"]["mode"] = "PLAYING"
        status["state"]["sonos"]["owned"] = True
        status["state"]["stream"]["active"] = True
        report = assess_idle_evidence(status, INTERNAL)
        self.assertEqual(report["assessment"], "blocked")
        self.assertFalse(report["write_authorized"])

    def test_group_cleanup_pending_is_blocked(self):
        status = copy.deepcopy(GOOD)
        status["state"]["sonos"]["group_cleanup_pending"] = True
        report = assess_idle_evidence(status, INTERNAL)
        self.assertIn("group_cleanup_not_confirmed_clear", report["reasons"])

    def test_hidden_reader_or_group_bindings_are_blocked(self):
        for name in ("reader_active", "server_alive", "group_joined",
                     "group_uncertain", "operation_in_progress"):
            with self.subTest(name=name):
                internal = dict(INTERNAL, **{name: True})
                report = assess_idle_evidence(GOOD, internal)
                self.assertEqual(report["assessment"], "blocked")
                self.assertFalse(report["write_authorized"])

    def test_unknown_or_missing_internal_fields_are_blocked(self):
        for name in INTERNAL:
            with self.subTest(name=name):
                internal = dict(INTERNAL)
                del internal[name]
                self.assertEqual(assess_idle_evidence(GOOD, internal)["assessment"],
                                 "blocked")

    def test_malformed_unknown_or_failed_status_is_blocked(self):
        for status in (None, {}, {"ok": False, "state": GOOD["state"]},
                       {"ok": True, "state": {}},
                       {"ok": True, "state": dict(GOOD["state"], schema_version=99)}):
            with self.subTest(status=status):
                report = assess_idle_evidence(status, INTERNAL)
                self.assertEqual(report["assessment"], "blocked")
                self.assertFalse(report["write_authorized"])

    def test_nonboolean_fields_do_not_count_as_false(self):
        for value in (0, None, "", "false"):
            with self.subTest(value=value):
                status = copy.deepcopy(GOOD)
                status["state"]["sonos"]["owned"] = value
                internal = dict(INTERNAL, reader_active=value)
                report = assess_idle_evidence(status, internal)
                self.assertEqual(report["assessment"], "blocked")
                self.assertFalse(report["write_authorized"])


if __name__ == "__main__":
    unittest.main()
