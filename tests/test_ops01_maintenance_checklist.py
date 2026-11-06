"""OPS-01 operational checklist with synthetic preflight evidence only."""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from tools.config_maintenance_checklist import build_maintenance_checklist, main


def sample_report(*, stable=True, reference="review_required",
                  player="active", sentinel="active"):
    return {
        "assessment": "blocked",
        "config": {
            "effective_path_resolved": True,
            "current_toml_readable": True,
            "template_checked": True,
            "proposal_status": reference,
            "two_observations_consistent": stable,
        },
        "units": {
            "sonoscdplayer-player.service": {"active_state": player},
            "sonoscdplayer-oled-shutdown-sentinel.service": {
                "active_state": sentinel},
        },
        "blockers": ["other_config_writers_not_excluded",
                     "operator_approval_missing"],
        "preserve": ["oled_shutdown_preserve_marker", "foreign_sonos_playback"],
    }


def checks(report):
    return {entry["id"]: entry for phase in report["checklist"]
            for entry in phase["checks"]}


class OperationalChecklistTests(unittest.TestCase):
    def test_seven_phases_in_expected_order(self):
        report = build_maintenance_checklist(sample_report())
        self.assertEqual([item["phase"] for item in report["checklist"]],
                         ["A_inventory", "B_operator_approval",
                          "C_controller_idle",
                          "D_exclusive_admission_and_writers",
                          "E_proposed_transaction",
                          "F_verification_and_recovery",
                          "G_resumption"])
        self.assertEqual(report["assessment"], "blocked")

    def test_stable_config_is_observation_never_writer_exclusion(self):
        result = build_maintenance_checklist(sample_report(
            stable=True, reference="compatible"))
        c = checks(result)
        self.assertEqual(c["two_identity_samples_consistent"]["status"],
                         "observed")
        self.assertEqual(c["external_writers_excluded"]["status"], "pending")
        self.assertEqual(c["socket_commands_and_refreshes_drained"]["status"],
                         "pending")
        self.assertFalse(result["ready_for_live_maintenance"])
        self.assertFalse(result["write_authorized"])

    def test_inactive_player_does_not_prove_controller_idle(self):
        result = build_maintenance_checklist(
            sample_report(player="inactive"))
        c = checks(result)
        self.assertEqual(c["player_state_observed_inactive"]["status"],
                         "observed")
        self.assertEqual(c["controller_idle_live_verified"]["status"],
                         "pending")
        self.assertFalse(result["stop_authorized"])

    def test_missing_or_unstable_file_never_looks_verified(self):
        input_report = sample_report(stable=False)
        input_report["config"]["current_toml_readable"] = False
        c = checks(build_maintenance_checklist(input_report))
        self.assertEqual(c["two_identity_samples_consistent"]["status"],
                         "pending")
        self.assertEqual(c["current_toml_readable"]["status"], "pending")

    def test_protected_missing_reference_stays_pending(self):
        result = build_maintenance_checklist(sample_report(
            reference="review_required"))
        self.assertEqual(checks(result)["reference_reviewed"]["status"],
                         "pending")
        self.assertFalse(result["write_authorized"])

    def test_sentinel_failure_requires_review_not_stop(self):
        result = build_maintenance_checklist(sample_report(
            sentinel="failed"))
        self.assertEqual(checks(result)["oled_sentinel_observed_active"]["status"],
                         "pending")
        self.assertIn("oled_shutdown_preserve_marker", result["preserve"])
        self.assertFalse(result["restart_authorized"])

    def test_all_observed_inventory_cannot_authorize_apply(self):
        result = build_maintenance_checklist(sample_report(
            stable=True, reference="compatible", player="inactive"))
        for key in ("separate_change_approval", "external_writers_excluded",
                    "separately_authorized_apply_available",
                    "resumption_separately_approved"):
            self.assertEqual(checks(result)[key]["status"], "pending")
        self.assertFalse(result["write_authorized"])

    def test_reject_invalid_preflight_shape(self):
        for value in (None, {}, {"config": []}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_maintenance_checklist(value)

    def test_cli_uses_repository_reference_and_redacts(self):
        from tools import config_maintenance_checklist as module
        expected = Path(module.__file__).resolve().parents[1] / "config.toml"
        with patch.object(module, "inspect_maintenance_preflight",
                          return_value=sample_report()) as inspected:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--repository-template"])
        inspected.assert_called_once_with(template_path=expected)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["write_authorized"])
        self.assertNotIn("PRIVATE_SECRET", out.getvalue())

    def test_cli_rejects_two_templates(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--template", "reference.toml",
                      "--repository-template"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
