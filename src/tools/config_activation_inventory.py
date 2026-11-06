"""Read-only OPS-01 systemd player activation exposure snapshot.

Queries effective systemd metadata only; never starts/stops/enables/masks
units or prints raw dependency names, command arguments, or file paths.
A snapshot CANNOT prove that a service cannot be activated later.
"""
from __future__ import annotations

import subprocess

_PLAYER = "sonoscdplayer-player.service"
_FIELDS = ("LoadState", "ActiveState", "UnitFileState", "Restart",
           "PartOf", "WantedBy", "RequiredBy", "UpheldBy",
           "TriggeredBy", "Triggers", "Sockets",
           "RefuseManualStart")
_RELATIONS = ("PartOf", "WantedBy", "RequiredBy", "UpheldBy",
              "TriggeredBy", "Triggers", "Sockets")


def inspect_player_activation(*, runner=subprocess.run):
    report = {
        "read_only": True,
        "metadata_available": False,
        "metadata_complete": False,
        "missing_properties": [],
        "unit_loaded": False,
        "service_active": None,
        "restart_policy": "unknown",
        "unit_file_state": "unknown",
        "activation_relationships": {name: "unknown" for name in _RELATIONS},
        "manual_start_refused": None,
        "activation_exclusion_verified": False,
        "stop_authorized": False,
        "restart_authorized": False,
        "write_authorized": False,
    }
    args = ["systemctl", "show", _PLAYER, "--no-pager"]
    args.extend("-p" + key for key in _FIELDS)
    try:
        result = runner(args, capture_output=True, text=True, timeout=5,
                        check=False)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return report
    if result.returncode != 0:
        return report
    fields = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in _FIELDS and key not in fields:
            fields[key] = value
    report["missing_properties"] = [k for k in _FIELDS if k not in fields]
    report["metadata_complete"] = not report["missing_properties"]
    if fields.get("LoadState") not in ("loaded", "not-found", "masked", "error"):
        return report
    if fields.get("ActiveState") not in ("active", "inactive", "failed", "activating",
                                    "deactivating", "reloading"):
        return report
    report["metadata_available"] = True
    report["unit_loaded"] = fields["LoadState"] == "loaded"
    report["service_active"] = fields["ActiveState"] == "active"
    # Only return explicitly allowlisted enums, never raw unit metadata.
    policy = fields.get("Restart")
    if policy in ("no", "always", "on-success", "on-failure",
                  "on-abnormal", "on-watchdog", "on-abort"):
        report["restart_policy"] = policy
    state = fields.get("UnitFileState")
    if state in ("enabled", "disabled", "static", "masked",
                 "indirect", "generated", "linked", "alias", "transient"):
        report["unit_file_state"] = state
    for name in _RELATIONS:
        if name in fields:
            report["activation_relationships"][name] = (
                "present" if fields[name].strip() else "none_observed")
    if fields.get("RefuseManualStart") in ("yes", "no"):
        report["manual_start_refused"] = fields["RefuseManualStart"] == "yes"
    return report
