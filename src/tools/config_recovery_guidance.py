"""OPS-01 offline recovery guidance for disposable sandbox transactions.

This is read-only operator guidance, not a recovery executor. It accepts only
an already-open sandbox directory descriptor and a reference template.
No target paths, file writes, service changes or production authorization.
"""
from __future__ import annotations

from .config_recovery_inspect import (
    RecoveryInspectionError, inspect_persisted_phase,
)


# Deliberately conservative: even verified bytes do not authorize a write.
# A delayed phase journal may reflect a completed atomic exchange, but its
# durability after an injected fsync error is not established by this report.
_GUIDANCE = {
    ("PREPARED", "not_replaced"): (
        "original_intact", "inspect_before_retry",
        "Original configuration matches its verified backup. The transaction "
        "was interrupted before replacement; review the artifacts before any retry."),
    ("PREPARED", "replacement_verified"): (
        "replacement_unconfirmed", "manual_review",
        "Candidate bytes are present, but the phase journal has not confirmed "
        "replacement. Review durability and obtain separate approval."),
    ("REPLACED", "replacement_verified"): (
        "replacement_unconfirmed", "manual_review",
        "Candidate bytes are present; final verification was not recorded. "
        "Review the interrupted transaction before any further action."),
    ("VERIFIED", "replacement_verified"): (
        "candidate_verified", "no_automatic_action",
        "Recorded verification matches candidate bytes. Retain backup and "
        "journal until the operator completes the separate maintenance review."),
    ("ROLLBACK_PREPARED", "replacement_verified"): (
        "rollback_unconfirmed", "manual_review",
        "The candidate remains present although rollback preparation was "
        "recorded. Do not assume restoration completed."),
    ("ROLLBACK_PREPARED", "not_replaced"): (
        "rollback_unconfirmed", "manual_review",
        "Original bytes are present but rollback completion was not recorded. "
        "Review durability before declaring restoration complete."),
    ("ROLLED_BACK", "not_replaced"): (
        "original_restored", "no_automatic_action",
        "Recorded rollback completion matches original bytes. Preserve "
        "transaction evidence until the maintenance review is complete."),
}


def recovery_guidance(dirfd: int, template: bytes) -> dict[str, object]:
    """Classify verified offline evidence without changing a single file.

    Unexpected, missing or inconsistent evidence becomes manual-review only;
    raw exception messages, configuration values and file paths are never
    included. Any outcome explicitly denies writes and service restarts.
    """
    try:
        phase, classification = inspect_persisted_phase(dirfd, template)
        condition, next_step, explanation = _GUIDANCE[(phase, classification)]
    except (RecoveryInspectionError, KeyError, TypeError, ValueError, OSError):
        return {
            "condition": "indeterminate",
            "phase": None,
            "configuration": "unverified",
            "next_step": "manual_review",
            "explanation": "Transaction evidence is missing, unsafe or inconsistent. "
                           "Preserve artifacts and inspect offline before any action.",
            "write_authorized": False,
            "restart_authorized": False,
        }
    return {
        "condition": condition,
        "phase": phase,
        "configuration": classification,
        "next_step": next_step,
        "explanation": explanation,
        "write_authorized": False,
        "restart_authorized": False,
    }
