"""OPS-01 consolidated read-only maintenance preflight.

Combines a systemd snapshot, effective player --config lookup, TOML parsing
and optional allowlisted migration proposal. No socket, Sonos, GPIO, drive,
systemd mutation, writing, approval or production maintenance authorization.
Never output configuration values, effective paths or ExecStart arguments.
"""
from __future__ import annotations

import argparse
import json
import subprocess

from .config_dry_run import InspectionError, effective_config_path, read_toml
from .config_maintenance_inventory import inspect_maintenance_runtime
from .config_migration_plan import plan_migration

_PLAYER = "sonoscdplayer-player.service"


def inspect_maintenance_preflight(*, template_path=None,
                                  runner=subprocess.run,
                                  config_reader=read_toml):
    """Return redacted diagnostic snapshot; no lease or authorization.

    Resolving an effective pathname does NOT verify stable file identity.
    The running player may still hold older parsed settings; service state,
    commands, writer exclusion and controller idle require separate proofs.
    """
    inventory = inspect_maintenance_runtime(runner=runner)
    blockers = set(inventory["blockers"])
    config = {
        "effective_path_resolved": False,
        "current_toml_readable": False,
        "template_checked": False,
        "proposal_status": "not_checked",
        "candidate_keys": [],
        "protected_missing_count": None,
        "unapproved_missing_count": None,
        "type_conflicts_count": None,
        "extra_local_keys_count": None,
    }
    current = None
    try:
        path = effective_config_path(_PLAYER, runner=runner)
        config["effective_path_resolved"] = True
        current = config_reader(path)
        config["current_toml_readable"] = True
    except (InspectionError, OSError, ValueError, TypeError):
        blockers.add("effective_player_config_unavailable")

    if template_path is None:
        blockers.add("reference_template_not_checked")
    elif current is None:
        blockers.add("template_comparison_not_possible")
    else:
        try:
            template = config_reader(template_path)
            proposal = plan_migration(current, template)
            config["template_checked"] = True
            config["proposal_status"] = proposal["status"]
            config["candidate_keys"] = proposal["candidates_for_explicit_review"]
            config["protected_missing_count"] = proposal["protected_missing_count"]
            config["unapproved_missing_count"] = proposal["unapproved_missing_count"]
            config["type_conflicts_count"] = len(proposal["type_conflicts"])
            config["extra_local_keys_count"] = proposal["extra_local_keys_count"]
            if proposal["status"] != "compatible":
                blockers.add("migration_proposal_requires_review")
        except (InspectionError, OSError, ValueError, TypeError):
            blockers.add("reference_template_unavailable_or_invalid")

    # A parsed configuration and a valid structural proposal do not prove
    # unchanged inode/bytes or exclude live concurrent modifications.
    return {
        "assessment": "blocked",
        "read_only": True,
        "write_authorized": False,
        "stop_authorized": False,
        "restart_authorized": False,
        "config": config,
        "units": inventory["units"],
        "blockers": sorted(blockers),
        "recommended_sequence": inventory["recommended_sequence"],
        "preserve": inventory["preserve"],
        "identity_stability_verified": False,
        "controller_idle_verified": False,
        "worker_drain_verified": False,
        "other_writers_excluded": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template",
                        help="optional reference TOML path for read-only proposal")
    args = parser.parse_args(argv)
    report = inspect_maintenance_preflight(template_path=args.template)
    print(json.dumps(report, sort_keys=True))
    return 0  # Diagnostic execution succeeded; never implies readiness.


if __name__ == "__main__":
    raise SystemExit(main())
