"""OPS-01 consolidated read-only preflight with synthetic systemd and TOML."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.config_maintenance_preflight import inspect_maintenance_preflight, main

CURRENT = ('[audio_startup]\nprebuffer_before_sonos=false\n'
           '[service]\nexperimental_safe_screen=true\n'
           'local_extra="PRIVATE_VALUE"\n')
TEMPLATE = ('[audio_startup]\nprebuffer_before_sonos=true\n'
            'pcm_timeout_seconds=45\n'
            '[service]\nexperimental_safe_screen=false\n')


def runner_for(current_path, *, player="active", fail_exec=False):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] != ["systemctl", "show"]:
            raise AssertionError("mutating systemd command")
        unit = argv[2]
        if argv[3:5] == ["-p", "ExecStart"]:
            if fail_exec:
                return subprocess.CompletedProcess(argv, 1, "", "")
            result = ("ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
                      "player_service.py --config " + str(current_path) +
                      " --socket /run/example.sock ; ignore_errors=no ; }\n")
        else:
            active = player if unit == "sonoscdplayer-player.service" else "active"
            result = ("LoadState=loaded\nActiveState=" + active +
                      "\nSubState=running\nPartOf=sonoscdplayer.target\n"
                      "ExecStart=private-secret-path\nExecStop=\n")
        return subprocess.CompletedProcess(argv, 0, result, "")
    return run, calls


class ConsolidatedPreflightTests(unittest.TestCase):
    def test_live_active_player_and_valid_template_still_block(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "local.toml"
            template = Path(directory) / "reference.toml"
            current.write_text(CURRENT)
            template.write_text(TEMPLATE)
            run, calls = runner_for(current)
            report = inspect_maintenance_preflight(
                template_path=template, runner=run)
            self.assertEqual(len(calls), 8)
            self.assertTrue(all(args[:2] == ["systemctl", "show"]
                                for args in calls))
            self.assertTrue(report["config"]["effective_path_resolved"])
            self.assertTrue(report["config"]["current_toml_readable"])
            self.assertTrue(report["config"]["template_checked"])
            self.assertEqual(report["config"]["candidate_keys"],
                             ["audio_startup.pcm_timeout_seconds"])
            self.assertIn("player_not_inactive", report["blockers"])
            self.assertIn("migration_proposal_requires_review", report["blockers"])
            self.assertFalse(report["write_authorized"])
            self.assertFalse(report["stop_authorized"])
            self.assertFalse(report["identity_stability_verified"])
            redacted = json.dumps(report)
            self.assertNotIn(str(current), redacted)
            self.assertNotIn(str(template), redacted)
            self.assertNotIn("PRIVATE_VALUE", redacted)
            self.assertNotIn("local_extra", redacted)
            self.assertNotIn("private-secret-path", redacted)

    def test_stable_snapshot_is_not_writer_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "local.toml"
            current.write_text(CURRENT)
            run, _ = runner_for(current)
            report = inspect_maintenance_preflight(runner=run)
            self.assertTrue(report["config"]["two_observations_consistent"])
            self.assertFalse(report["writer_coordination"]["live_writer_exclusion_proven"])
            self.assertEqual(report["writer_coordination"]["ops01_transaction"],
                             "offline_sandbox_only")
            self.assertIn("other_config_writers_not_excluded", report["blockers"])
            self.assertFalse(report["write_authorized"])

    def test_inconclusive_identity_snapshot_is_explicit_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "local.toml"
            current.write_text(CURRENT)
            run, _ = runner_for(current)
            from tools import config_maintenance_preflight as module
            with patch.object(module, "sample_config_identity",
                              return_value={"two_observations_consistent": False}):
                report = inspect_maintenance_preflight(runner=run)
            self.assertFalse(report["config"]["two_observations_consistent"])
            self.assertIn("config_identity_sample_inconclusive", report["blockers"])
            self.assertFalse(report["write_authorized"])

    def test_no_template_means_not_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "local.toml"
            current.write_text(CURRENT)
            run, _ = runner_for(current)
            report = inspect_maintenance_preflight(runner=run)
            self.assertTrue(report["config"]["current_toml_readable"])
            self.assertFalse(report["config"]["template_checked"])
            self.assertIn("reference_template_not_checked", report["blockers"])
            self.assertEqual(report["config"]["proposal_status"], "not_checked")

    def test_inactive_player_cannot_grant_maintenance(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "local.toml"
            current.write_text(CURRENT)
            run, _ = runner_for(current, player="inactive")
            report = inspect_maintenance_preflight(runner=run)
            self.assertNotIn("player_not_inactive", report["blockers"])
            self.assertIn("other_config_writers_not_excluded", report["blockers"])
            self.assertFalse(report["worker_drain_verified"])
            self.assertFalse(report["restart_authorized"])

    def test_unavailable_effective_execstart_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "local.toml"
            current.write_text(CURRENT)
            run, _ = runner_for(current, fail_exec=True)
            report = inspect_maintenance_preflight(runner=run)
            self.assertFalse(report["config"]["effective_path_resolved"])
            self.assertFalse(report["config"]["current_toml_readable"])
            self.assertIn("effective_player_config_unavailable",
                          report["blockers"])
            self.assertFalse(report["write_authorized"])

    def test_invalid_current_toml_is_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "local.toml"
            current.write_text('[secret\nPRIVATE_VALUE')
            run, _ = runner_for(current)
            report = inspect_maintenance_preflight(runner=run)
            self.assertTrue(report["config"]["effective_path_resolved"])
            self.assertFalse(report["config"]["current_toml_readable"])
            self.assertIn("effective_player_config_unavailable",
                          report["blockers"])
            self.assertNotIn("PRIVATE_VALUE", json.dumps(report))

    def test_bad_template_does_not_expose_content(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "local.toml"
            template = Path(directory) / "reference.toml"
            current.write_text(CURRENT)
            template.write_text('[secret\nPRIVATE_TEMPLATE_VALUE')
            run, _ = runner_for(current)
            report = inspect_maintenance_preflight(
                runner=run, template_path=template)
            self.assertFalse(report["config"]["template_checked"])
            self.assertIn("reference_template_unavailable_or_invalid",
                          report["blockers"])
            self.assertNotIn("PRIVATE_TEMPLATE_VALUE", json.dumps(report))

    def test_repository_template_flag_uses_versioned_src_config(self):
        from tools import config_maintenance_preflight as module
        expected = Path(module.__file__).resolve().parents[1] / "config.toml"
        with patch.object(module, "inspect_maintenance_preflight",
                          return_value={"assessment": "blocked",
                                        "write_authorized": False}) as inspection:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--repository-template"]), 0)
        inspection.assert_called_once_with(template_path=expected)

    def test_repository_template_and_explicit_path_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--repository-template", "--template", "other.toml"])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_outputs_one_redacted_json_report(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "local.toml"
            current.write_text(CURRENT)
            run, _ = runner_for(current)
            # The CLI uses the actual inspection function; patch only its
            # default external command runner in the test.
            from tools import config_maintenance_preflight as module
            with patch.object(module, "inspect_maintenance_preflight",
                              return_value=inspect_maintenance_preflight(
                                  runner=run)) as inspection:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = main([])
            self.assertEqual(status, 0)
            self.assertEqual(inspection.call_count, 1)
            report = json.loads(output.getvalue())
            self.assertEqual(report["assessment"], "blocked")
            self.assertFalse(report["write_authorized"])


if __name__ == "__main__":
    unittest.main()
