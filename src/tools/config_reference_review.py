"""OPS-01 narrowly scoped read-only review of a known reference omission.

An operator may explicitly acknowledge ONLY the known missing
service.experimental_safe_screen=false in the reference template. This does
not change any TOML, compare existing values, approve a migration, or remove
the maintenance authorization blockers.
"""
from __future__ import annotations

from .config_migration_plan import plan_migration


def assess_known_safe_screen_omission(current, template, *, acknowledged=False):
    """Fail closed unless the *only* structural difference is the named omission."""
    result = {"status": "not_acknowledged", "key": None,
              "write_authorized": False, "existing_values_compared": False}
    if acknowledged is not True:
        return result
    if not isinstance(current, dict) or not isinstance(template, dict):
        return {**result, "status": "not_applicable"}
    actual_service = current.get("service")
    reference_service = template.get("service")
    if (not isinstance(actual_service, dict)
            or not isinstance(reference_service, dict)
            or "experimental_safe_screen" in actual_service
            or reference_service.get("experimental_safe_screen") is not False):
        return {**result, "status": "not_applicable"}
    proposal = plan_migration(current, template)
    if (proposal["status"] != "review_required"
            or proposal["protected_missing_count"] != 1
            or proposal["unapproved_missing_count"] != 0
            or proposal["type_conflicts"]
            or proposal["extra_local_keys_count"] != 0
            or proposal["candidates_for_explicit_review"]):
        return {**result, "status": "not_applicable"}
    return {**result, "status": "accepted_known_omission",
            "key": "service.experimental_safe_screen"}
