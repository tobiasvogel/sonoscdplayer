"""OPS-01 read-only systemd maintenance inventory for the CD player.

This diagnostic invokes only `systemctl show` and never uses the control
socket, touches the optical drive, signals a process or edits configuration.
Its output contains no effective path, command-line arguments, or UID.
A systemd snapshot cannot establish controller idle or writer exclusion.
"""
from __future__ import annotations

import subprocess

_PLAYER = "sonoscdplayer-player.service"
_BUTTONS = "sonoscdplayer-buttons.service"
_OLED = "sonoscdplayer-oled.service"
_LED = "sonoscdplayer-led.service"
_SENTINEL = "sonoscdplayer-oled-shutdown-sentinel.service"
_TARGET = "sonoscdplayer.target"
_UNITS = (_TARGET, _PLAYER, _BUTTONS, _OLED, _LED, _SENTINEL)
_PROPERTIES = (
    "LoadState", "ActiveState", "SubState", "UnitFileState",
    "Restart", "PartOf", "ExecStart", "ExecStop", "TimeoutStopUSec",
)
_STATES = frozenset({"active", "inactive", "failed", "activating",
                     "deactivating", "reloading"})


def _show(unit, runner):
    cmd = ["systemctl", "show", unit, "--no-pager"]
    cmd.extend("-p" + property_name for property_name in _PROPERTIES)
    try:
        result = runner(cmd, capture_output=True, text=True,
                        timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return None
    if result.returncode != 0:
        return None
    fields = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in _PROPERTIES and key not in fields:
            fields[key] = value
    if (any(name not in fields for name in ("LoadState", "ActiveState",
                                            "SubState", "ExecStart", "PartOf"))
            or fields["ActiveState"] not in _STATES):
        return None
    # Explicitly discard ExecStart/ExecStop text; they may contain private
    # paths, command-line parameters and other machine-specific values.
    return fields


def inspect_maintenance_runtime(*, runner=subprocess.run):
    """Return redacted inventory and conservative next steps; no authorization.

    Missing, changed or unrecognizable systemd evidence is a blocker. All
    results are advisory snapshots; no code here acquires a maintenance lease.
    """
    records = {unit: _show(unit, runner) for unit in _UNITS}
    issues = []
    for unit, record in records.items():
        if record is None:
            issues.append("unavailable_" + unit.split(".")[0])
        elif record["LoadState"] != "loaded":
            issues.append("unit_not_loaded_" + unit.split(".")[0])

    player = records[_PLAYER]
    target = records[_TARGET]
    sentinel = records[_SENTINEL]
    if player is not None:
        if player["ActiveState"] != "inactive":
            issues.append("player_not_inactive")
        if _TARGET not in player["PartOf"].split():
            issues.append("player_target_relationship_unexpected")
        # A service stop would run the player shutdown handler. In the
        # deployed configuration it may stop verified owned Sonos playback.
        if player["ActiveState"] == "active":
            issues.append("player_stop_may_affect_owned_sonos")
    if target is not None and target["ActiveState"] == "active":
        issues.append("do_not_stop_composite_target")
    if sentinel is not None and sentinel["ActiveState"] != "active":
        issues.append("sentinel_state_needs_review")

    # The systemd snapshot cannot show in-flight socket commands, controller
    # group ownership, optical reader, or independent config writers.
    issues.extend((
        "controller_idle_not_verified",
        "socket_and_worker_drain_not_verified",
        "other_config_writers_not_excluded",
        "effective_config_identity_not_verified",
        "operator_approval_missing",
    ))
    summaries = {}
    for unit, record in records.items():
        summaries[unit] = {
            "available": record is not None,
            "loaded": record is not None and record["LoadState"] == "loaded",
            "active_state": record["ActiveState"] if record else "unknown",
            "sub_state": (
                record["SubState"] if record
                and record["SubState"] in ("running", "exited", "dead", "failed")
                else "unknown"),
        }
    return {
        "assessment": "blocked",
        "read_only": True,
        "write_authorized": False,
        "stop_authorized": False,
        "restart_authorized": False,
        "units": summaries,
        "blockers": sorted(set(issues)),
        "recommended_sequence": [
            "review_systemd_inventory_and_effective_config",
            "obtain_separate_maintenance_approval",
            "verify_player_idle_and_group_ownership_in_serial_worker",
            "drain_command_and_refresh_admission",
            "exclude_external_config_writers_and_service_activation",
            "recheck_config_identity_and_candidate",
            "run_separately_authorized_transaction_and_verification",
            "review_service_resumption_without_touching_oled_sentinel",
        ],
        "preserve": [
            _SENTINEL,
            "oled_shutdown_preserve_marker",
            "local_shutdown_hooks",
            "foreign_sonos_playback",
        ],
    }
