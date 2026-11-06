"""OPS-01 exact known-omission review and redacted systemd activation tests."""
import contextlib
import io
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.config_reference_review import assess_known_safe_screen_omission
from tools.config_activation_inventory import inspect_player_activation
from tools.config_maintenance_checklist import build_maintenance_checklist, main
from tools.config_maintenance_preflight import inspect_maintenance_preflight


def current_config():
    return {"service": {"shutdown_policy": "none"}}


def template_config():
    return {"service": {"shutdown_policy": "none",
                        "experimental_safe_screen": False}}


def activation_runner(*, state="active", extra="", omit=None):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        fields = {"LoadState": "loaded", "ActiveState": state,
                  "UnitFileState": "enabled", "Restart": "on-failure",
                  "PartOf": "sonoscdplayer.target", "WantedBy": "sonoscdplayer.target",
                  "RequiredBy": "", "UpheldBy": "", "TriggeredBy": extra,
                  "Triggers": "", "Sockets": "", "RefuseManualStart": "no"}
        if omit:
            del fields[omit]
        return subprocess.CompletedProcess(argv, 0,
                    "".join(f"{k}={v}\n" for k, v in fields.items()), "")
    return run, calls


class ReviewAndActivationTests(unittest.TestCase):
    def test_exact_missing_false_can_be_acknowledged(self):
        r = assess_known_safe_screen_omission(
            current_config(), template_config(), acknowledged=True)
        self.assertEqual(r["status"], "accepted_known_omission")
        self.assertEqual(r["key"], "service.experimental_safe_screen")
        self.assertFalse(r["write_authorized"])

    def test_acknowledgement_is_explicit(self):
        self.assertEqual(assess_known_safe_screen_omission(
            current_config(), template_config())["status"], "not_acknowledged")

    def test_other_missing_key_invalidates_exception(self):
        t = template_config()
        t["service"]["other_setting"] = True
        self.assertEqual(assess_known_safe_screen_omission(
            current_config(), t, acknowledged=True)["status"], "not_applicable")

    def test_wrong_default_invalidates_exception(self):
        t = template_config()
        t["service"]["experimental_safe_screen"] = True
        self.assertEqual(assess_known_safe_screen_omission(
            current_config(), t, acknowledged=True)["status"], "not_applicable")

    def test_present_key_cannot_be_acknowledged_as_missing(self):
        c = current_config()
        c["service"]["experimental_safe_screen"] = True
        self.assertEqual(assess_known_safe_screen_omission(
            c, template_config(), acknowledged=True)["status"], "not_applicable")

    def test_extra_local_key_invalidates_exception(self):
        c = current_config()
        c["service"]["local_only"] = "PRIVATE_VALUE"
        r = assess_known_safe_screen_omission(c, template_config(),
                                              acknowledged=True)
        self.assertEqual(r["status"], "not_applicable")
        self.assertNotIn("PRIVATE_VALUE", repr(r))

    def test_systemd_activation_snapshot_is_redacted(self):
        runner, calls = activation_runner(extra="private-name.socket")
        report = inspect_player_activation(runner=runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3],
                         ["systemctl", "show", "sonoscdplayer-player.service"])
        self.assertTrue(report["metadata_available"])
        self.assertEqual(report["restart_policy"], "on-failure")
        self.assertEqual(report["activation_relationships"]["TriggeredBy"],
                         "present")
        self.assertFalse(report["activation_exclusion_verified"])
        self.assertFalse(report["stop_authorized"])
        self.assertNotIn("private-name", json.dumps(report))

    def test_systemd_missing_metadata_fails_closed(self):
        runner, _ = activation_runner(omit="PartOf")
        report = inspect_player_activation(runner=runner)
        self.assertTrue(report["metadata_available"])
        self.assertFalse(report["metadata_complete"])
        self.assertIn("PartOf", report["missing_properties"])
        self.assertEqual(report["activation_relationships"]["PartOf"], "unknown")
        self.assertEqual(report["activation_relationships"]["WantedBy"], "present")
        self.assertFalse(report["write_authorized"])

    def test_systemd_execution_failure_fails_closed(self):
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 5)
        report = inspect_player_activation(runner=run)
        self.assertFalse(report["metadata_available"])
        self.assertFalse(report["activation_exclusion_verified"])

    def test_partial_activation_data_is_visible_but_not_authorizing(self):
        def run(argv, **kwargs):
            output = ("LoadState=loaded\nActiveState=active\n"
                      "UnitFileState=enabled\nRestart=on-failure\n"
                      "PartOf=sonoscdplayer.target\nWantedBy=sonoscdplayer.target\n")
            return subprocess.CompletedProcess(argv, 0, output, "")
        report = inspect_player_activation(runner=run)
        self.assertTrue(report["metadata_available"])
        self.assertFalse(report["metadata_complete"])
        self.assertTrue(report["unit_loaded"])
        self.assertTrue(report["service_active"])
        self.assertEqual(report["restart_policy"], "on-failure")
        self.assertEqual(report["activation_relationships"]["PartOf"], "present")
        self.assertEqual(report["activation_relationships"]["TriggeredBy"], "unknown")
        self.assertIn("TriggeredBy", report["missing_properties"])
        self.assertFalse(report["activation_exclusion_verified"])
        self.assertFalse(report["write_authorized"])

    def test_partial_metadata_does_not_complete_checklist(self):
        report = {
            "config": {"template_checked": True, "proposal_status": "compatible"},
            "units": {},
            "activation": {"metadata_available": True, "metadata_complete": False},
            "blockers": [],
        }
        result = build_maintenance_checklist(report)
        checks = {c["id"]: c for p in result["checklist"] for c in p["checks"]}
        self.assertEqual(checks["activation_metadata_inspected"]["status"],
                         "pending")
        self.assertFalse(result["ready_for_live_maintenance"])

    def test_inactive_player_does_not_prove_no_activation(self):
        runner, _ = activation_runner(state="inactive")
        report = inspect_player_activation(runner=runner)
        self.assertFalse(report["service_active"])
        self.assertFalse(report["activation_exclusion_verified"])
        self.assertEqual(report["unit_file_state"], "enabled")

    def test_checklist_accepts_known_omission_but_blocks_all_actions(self):
        report = {
            "config": {"effective_path_resolved": True, "current_toml_readable": True,
                       "two_observations_consistent": True, "template_checked": True,
                       "proposal_status": "review_required",
                       "reference_review": {"status": "accepted_known_omission",
                                            "key": "service.experimental_safe_screen"}},
            "units": {},
            "activation": {"metadata_available": True,
                           "metadata_complete": True,
                           "activation_exclusion_verified": False},
            "blockers": ["migration_proposal_requires_review",
                         "other_config_writers_not_excluded"],
        }
        result = build_maintenance_checklist(report)
        checks = {c["id"]: c for p in result["checklist"] for c in p["checks"]}
        self.assertEqual(checks["reference_reviewed"]["status"], "observed")
        self.assertEqual(checks["activation_metadata_inspected"]["status"],
                         "observed")
        self.assertEqual(checks["service_reactivation_excluded"]["status"],
                         "pending")
        self.assertIn("migration_proposal_requires_review", result["blockers"])
        self.assertFalse(result["ready_for_live_maintenance"])
        self.assertFalse(result["write_authorized"])

    def test_cli_flag_is_forwarded_only_on_explicit_use(self):
        from tools import config_maintenance_checklist as module
        with patch.object(module, "inspect_maintenance_preflight",
                          return_value={"config": {}, "units": {},
                                        "blockers": []}) as inspected:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--repository-template",
                                       "--acknowledge-safe-screen-omission"]), 0)
        self.assertTrue(inspected.call_args.kwargs[
            "acknowledge_safe_screen_omission"])

    def test_live_preflight_synthetic_omission_stays_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            current = Path(root) / "local.toml"
            template = Path(root) / "template.toml"
            current.write_text('[service]\nshutdown_policy="none"\n')
            template.write_text('[service]\nshutdown_policy="none"\n'
                                'experimental_safe_screen=false\n')

            def run(argv, **kwargs):
                if argv[3:5] == ["-p", "ExecStart"]:
                    out = ("ExecStart={ path=/usr/bin/python3 ; argv[]="
                           "/usr/bin/python3 player_service.py --config "
                           + str(current) +
                           " --socket /run/synthetic.sock ; ignore_errors=no ; }\n")
                else:
                    out = ("LoadState=loaded\nActiveState=active\n"
                           "SubState=running\nPartOf=sonoscdplayer.target\n")
                return subprocess.CompletedProcess(argv, 0, out, "")

            report = inspect_maintenance_preflight(
                runner=run, template_path=template,
                acknowledge_safe_screen_omission=True)
            self.assertEqual(report["config"]["reference_review"]["status"],
                             "accepted_known_omission")
            self.assertEqual(report["assessment"], "blocked")
            self.assertFalse(report["write_authorized"])
            self.assertFalse(report["activation"]["activation_exclusion_verified"])


if __name__ == "__main__":
    unittest.main()
