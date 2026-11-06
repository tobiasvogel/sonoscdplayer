"""Read-only OPS-01 systemd coordination inventory; no real systemd calls."""
import subprocess
import unittest

from tools.config_maintenance_inventory import inspect_maintenance_runtime

UNITS = (
    "sonoscdplayer.target",
    "sonoscdplayer-player.service",
    "sonoscdplayer-buttons.service",
    "sonoscdplayer-oled.service",
    "sonoscdplayer-led.service",
    "sonoscdplayer-oled-shutdown-sentinel.service",
)


def fake_runner(*, player="active", target="active", sentinel="active",
                oled="active", unavailable=(), malformed=(),
                target_omits_service_properties=False):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        unit = argv[2]
        if unit in unavailable:
            return subprocess.CompletedProcess(argv, 1, "", "missing")
        if unit in malformed:
            return subprocess.CompletedProcess(argv, 0, "unexpected", "")
        status = ("active" if unit not in
                  ("sonoscdplayer-player.service", "sonoscdplayer.target",
                   "sonoscdplayer-oled-shutdown-sentinel.service")
                  else {UNITS[1]: player, UNITS[0]: target,
                        UNITS[5]: sentinel}[unit])
        if unit == UNITS[3]:
            status = oled
        substate = ("running" if status == "active" else
                    "failed" if status == "failed" else "dead")
        text = (
            "LoadState=loaded\n"
            "ActiveState=" + status + "\n"
            "SubState=" + substate + "\n"
            "PartOf=" + ("sonoscdplayer.target" if unit == UNITS[1]
                         else "") + "\n"
            "ExecStart={ path=/private/example; argv[]=private-uid }\n"
            "ExecStop=\n"
            "Restart=no\n"
        )
        if target_omits_service_properties and unit == UNITS[0]:
            text = ("LoadState=loaded\\nActiveState=" + status +
                    "\\nSubState=" + substate + "\\n")
        return subprocess.CompletedProcess(argv, 0, text, "")
    return run, calls


class MaintenanceInventoryTests(unittest.TestCase):
    def assert_only_reads(self, calls):
        self.assertEqual(len(calls), len(UNITS))
        self.assertEqual({args[2] for args, _ in calls}, set(UNITS))
        for args, kwargs in calls:
            self.assertEqual(args[:2], ["systemctl", "show"])
            self.assertIn("--no-pager", args)
            self.assertEqual(kwargs["timeout"], 5)
            self.assertIs(kwargs["check"], False)

    def test_active_player_is_not_stop_authorized(self):
        runner, calls = fake_runner()
        report = inspect_maintenance_runtime(runner=runner)
        self.assert_only_reads(calls)
        self.assertEqual(report["assessment"], "blocked")
        self.assertIn("player_not_inactive", report["blockers"])
        self.assertIn("player_stop_may_affect_owned_sonos", report["blockers"])
        self.assertIn("do_not_stop_composite_target", report["blockers"])
        self.assertFalse(report["write_authorized"])
        self.assertFalse(report["stop_authorized"])
        self.assertFalse(report["restart_authorized"])
        self.assertNotIn("private-uid", repr(report))

    def test_inactive_player_still_has_external_blockers(self):
        runner, calls = fake_runner(player="inactive", target="inactive")
        report = inspect_maintenance_runtime(runner=runner)
        self.assert_only_reads(calls)
        self.assertNotIn("player_not_inactive", report["blockers"])
        self.assertIn("controller_idle_not_verified", report["blockers"])
        self.assertIn("other_config_writers_not_excluded", report["blockers"])
        self.assertFalse(report["write_authorized"])

    def test_target_without_service_only_fields_is_still_available(self):
        runner, calls = fake_runner(target_omits_service_properties=True)
        report = inspect_maintenance_runtime(runner=runner)
        self.assert_only_reads(calls)
        self.assertTrue(report["units"][UNITS[0]]["available"])
        self.assertTrue(report["units"][UNITS[0]]["loaded"])
        self.assertEqual(report["units"][UNITS[0]]["active_state"], "active")
        self.assertIn("do_not_stop_composite_target", report["blockers"])
        self.assertNotIn("unavailable_sonoscdplayer", report["blockers"])

    def test_failed_oled_is_reported_as_explicit_blocker(self):
        runner, calls = fake_runner(oled="failed")
        report = inspect_maintenance_runtime(runner=runner)
        self.assert_only_reads(calls)
        self.assertEqual(report["units"][UNITS[3]]["active_state"], "failed")
        self.assertIn("unit_failed_sonoscdplayer-oled", report["blockers"])
        self.assertFalse(report["stop_authorized"])
        self.assertFalse(report["restart_authorized"])

    def test_unavailable_service_fails_conservatively(self):
        runner, _ = fake_runner(unavailable={UNITS[1]})
        report = inspect_maintenance_runtime(runner=runner)
        self.assertIn("unavailable_sonoscdplayer-player", report["blockers"])
        self.assertFalse(report["units"][UNITS[1]]["available"])
        self.assertFalse(report["stop_authorized"])

    def test_malformed_systemd_record_fails_conservatively(self):
        runner, _ = fake_runner(malformed={UNITS[5]})
        report = inspect_maintenance_runtime(runner=runner)
        self.assertIn("unavailable_sonoscdplayer-oled-shutdown-sentinel",
                      report["blockers"])
        self.assertFalse(report["write_authorized"])

    def test_inactive_sentinel_requires_review_not_stop(self):
        runner, _ = fake_runner(sentinel="inactive")
        report = inspect_maintenance_runtime(runner=runner)
        self.assertIn("sentinel_state_needs_review", report["blockers"])
        self.assertIn(UNITS[5], report["preserve"])
        self.assertFalse(report["stop_authorized"])

    def test_runner_failure_never_becomes_approval(self):
        def broken(_argv, **_kwargs):
            raise subprocess.TimeoutExpired("systemctl", timeout=5)
        report = inspect_maintenance_runtime(runner=broken)
        self.assertEqual(sum(not item["available"] for item in
                             report["units"].values()), len(UNITS))
        self.assertEqual(report["assessment"], "blocked")
        self.assertFalse(report["write_authorized"])

    def test_sequence_explicitly_excludes_automatic_restart(self):
        runner, _ = fake_runner()
        report = inspect_maintenance_runtime(runner=runner)
        steps = report["recommended_sequence"]
        self.assertLess(steps.index("drain_command_and_refresh_admission"),
                        steps.index("exclude_external_config_writers_and_service_activation"))
        self.assertLess(steps.index("recheck_config_identity_and_candidate"),
                        steps.index("run_separately_authorized_transaction_and_verification"))
        self.assertTrue(report["read_only"])
        self.assertFalse(report["restart_authorized"])


if __name__ == "__main__":
    unittest.main()
