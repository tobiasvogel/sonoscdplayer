"""OPS-01 read-only operational maintenance checklist.

Translate the existing redacted preflight into concrete phase-by-phase checks.
No service stop, socket access, Sonos commands, filesystem writes, permission
grant, or production apply. Checklist completion is never authorization.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config_maintenance_preflight import inspect_maintenance_preflight


def build_maintenance_checklist(report):
    """Summarize already-observed evidence and distinct outstanding gates.

    All statuses are observations or blockers. In particular a stable config
    sample, inactive service, or a compatible template cannot establish an
    exclusive maintenance window.
    """
    if not isinstance(report, dict) or not isinstance(report.get("config"), dict):
        raise ValueError("invalid redacted preflight")
    cfg = report["config"]
    blockers = set(report.get("blockers", ()))
    units = report.get("units", {})
    player = units.get("sonoscdplayer-player.service", {})
    sentinel = units.get("sonoscdplayer-oled-shutdown-sentinel.service", {})

    def check(key, observed, next_action):
        return {
            "id": key,
            "status": "observed" if observed else "pending",
            "next_action": next_action,
        }

    phases = [
        {"phase": "A_inventory",
         "checks": [
             check("effective_config_found",
                   cfg.get("effective_path_resolved") is True,
                   "inspect_effective_player_execstart_read_only"),
             check("current_toml_readable",
                   cfg.get("current_toml_readable") is True,
                   "inspect_current_config_read_only"),
             check("reference_reviewed",
                   cfg.get("template_checked") is True
                   and cfg.get("proposal_status") == "compatible",
                   "review_reference_discrepancies_without_auto_apply"),
             check("two_identity_samples_consistent",
                   cfg.get("two_observations_consistent") is True,
                   "repeat_identity_checks_before_any_future_change"),
             check("oled_sentinel_observed_active",
                   sentinel.get("active_state") == "active",
                   "preserve_shutdown_sentinel_and_local_hooks"),
         ]},
        {"phase": "B_operator_approval",
         "checks": [check("separate_change_approval", False,
                          "approve_exact_target_candidate_rollback_and_service_effects")]},
        {"phase": "C_controller_idle",
         "checks": [
             check("controller_idle_live_verified", False,
                   "verify_stopped_no_reader_stream_owned_sonos_or_group_recovery"),
             check("player_state_observed_inactive",
                   player.get("active_state") == "inactive",
                   "review_playback_ownership_before_any_player_stop"),
         ]},
        {"phase": "D_exclusive_admission_and_writers",
         "checks": [
             check("socket_commands_and_refreshes_drained", False,
                   "establish_live_serial_worker_fence"),
             check("service_reactivation_excluded", False,
                   "plan_activation_exclusion_without_stopping_composite_target"),
             check("external_writers_excluded", False,
                   "coordinate_editors_installers_deployments_and_stable_lock"),
         ]},
        {"phase": "E_proposed_transaction",
         "checks": [
             check("candidate_and_original_revalidated", False,
                   "recheck_original_inode_hash_and_exact_approved_candidate"),
             check("separately_authorized_apply_available", False,
                   "no_production_apply_implemented"),
         ]},
        {"phase": "F_verification_and_recovery",
         "checks": [check("transaction_and_recovery_verified", False,
                          "review_backup_journal_checksums_and_recovery_state")]},
        {"phase": "G_resumption",
         "checks": [check("resumption_separately_approved", False,
                          "verify_service_sonos_and_oled_impacts_before_resume")]},
    ]
    return {
        "assessment": "blocked",
        "read_only": True,
        "write_authorized": False,
        "stop_authorized": False,
        "restart_authorized": False,
        "ready_for_live_maintenance": False,
        "checklist": phases,
        "blockers": sorted(blockers),
        "preserve": report.get("preserve", []),
        "note": "observed_is_not_exclusive_proof_or_approval",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--template", help="read-only comparison reference")
    source.add_argument("--repository-template", action="store_true",
                        help="compare with versioned src/config.toml")
    args = parser.parse_args(argv)
    template = (Path(__file__).resolve().parents[1] / "config.toml"
                if args.repository_template else args.template)
    report = inspect_maintenance_preflight(template_path=template)
    print(json.dumps(build_maintenance_checklist(report), sort_keys=True))
    return 0  # Inspection executed, NOT maintenance approval.


if __name__ == "__main__":
    raise SystemExit(main())
