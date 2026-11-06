"""OPS-01 read-only, conservative player-idle evidence assessment.

This is NOT a production authorization API. Cached player status cannot prove
there is no active reader, joined group, or concurrent operation. The returned
result always requires separately implemented live checks and a writer lock.
No socket, service, Sonos, drive or filesystem access occurs in this module.
"""
from __future__ import annotations


_REQUIRED_INTERNAL = ("reader_active", "server_alive", "group_joined",
                      "group_uncertain", "operation_in_progress")


def assess_idle_evidence(snapshot, internal=None):
    """Assess synthetic evidence. Never grant permission for configuration writes.

    `internal` represents an independent *hypothetical* controller-level
    observation, not the public status protocol. Its existence alone cannot
    establish freshness or eliminate races.
    """
    reasons = []
    if not isinstance(snapshot, dict) or snapshot.get("ok") is not True:
        reasons.append("invalid_status_response")
        state = None
    else:
        state = snapshot.get("state")

    if not isinstance(state, dict) or type(state.get("schema_version")) is not int or state.get("schema_version") != 1:
        reasons.append("unknown_schema")
        state = {}

    player = state.get("player")
    sonos = state.get("sonos")
    stream = state.get("stream")
    if not isinstance(player, dict) or player.get("mode") != "STOPPED":
        reasons.append("player_not_confirmed_stopped")
    if not isinstance(sonos, dict) or sonos.get("owned") is not False:
        reasons.append("ownership_not_confirmed_released")
    if not isinstance(sonos, dict) or sonos.get("group_cleanup_pending") is not False:
        reasons.append("group_cleanup_not_confirmed_clear")
    if not isinstance(stream, dict) or stream.get("active") is not False:
        reasons.append("stream_not_confirmed_inactive")

    if not isinstance(internal, dict):
        reasons.append("missing_controller_level_evidence")
    else:
        for key in _REQUIRED_INTERNAL:
            if internal.get(key) is not False:
                reasons.append("controller_" + key + "_not_confirmed_clear")

    # This is only static evidence assessment. It cannot authorize file writes:
    # the cached API may be stale, and no exclusive coordination exists.
    return {
        "assessment": "blocked" if reasons else "incomplete_live_verification",
        "evidence_consistent_with_idle": not reasons,
        "write_authorized": False,
        "requires_live_exclusive_preflight": True,
        "reasons": sorted(set(reasons)),
    }
