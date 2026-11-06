"""OPS-01 offline maintenance-evidence gate (no I/O, no write authorization).

This only checks that a claimed set of observations is internally consistent.
It CANNOT prove those observations are true or fresh, coordinate with the
player, stop a service, exclude noncooperating writers, or authorize apply.
"""
from __future__ import annotations

import re

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED = (
    "operator_approved",
    "player_service_inactive",
    "player_process_absent",
    "control_socket_listener_absent",
    "reader_process_absent",
    "group_recovery_resolved",
    "other_config_writers_excluded",
    "private_directory_verified",
    "exclusive_writer_lock_held",
)


def assess_maintenance_evidence(evidence, *, expected_original_sha256: str,
                                expected_candidate_sha256: str):
    """Return redacted decision; never grant permission to touch files.

    True on every key means a *claimed* condition. Unverified assertions do
    not constitute a maintenance lease or a production preflight.
    """
    problems = []
    if not isinstance(evidence, dict):
        evidence = {}
        problems.append("missing_evidence")
    for key in _REQUIRED:
        if evidence.get(key) is not True:
            problems.append("not_verified_" + key)

    for name, expected in (
        ("original_sha256", expected_original_sha256),
        ("candidate_sha256", expected_candidate_sha256),
    ):
        if (not isinstance(expected, str) or not _SHA256.fullmatch(expected) or
                evidence.get(name) != expected):
            problems.append("invalid_" + name)

    if (not isinstance(evidence.get("maintenance_window_id"), str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{8,64}",
                             evidence["maintenance_window_id"])):
        problems.append("invalid_maintenance_window_id")

    # A successful policy check is not a live lock, not a maintenance token,
    # and not evidence that another process cannot race os.replace.
    return {
        "assessment": "blocked" if problems else "claimed_conditions_consistent",
        "write_authorized": False,
        "live_verification_required": True,
        "noncooperating_writer_exclusion_unproven": True,
        "reasons": sorted(set(problems)),
    }
