"""OPS-01 internal controller idle sample, for future serialized worker use.

No socket command, Sonos/network IO, hardware access, systemd interaction or
config writes. Collect the sample only while serialized controller mutations
have been drained. Merely collecting this sample does NOT authorize freezes
or writes; the barrier and systemd/writer exclusions remain separate gates.
"""
from __future__ import annotations

from .config_maintenance_barrier import IdleProof


def _is_false(value):
    return type(value) is bool and value is False


def _is_empty(value, expected_type):
    return type(value) is expected_type and not value


def sample_controller_idle(controller) -> IdleProof:
    """Conservative direct in-memory sample, never from cached status.

    Unknown attributes, unrecognized modes, unexpected group state and
    inaccessible process state all fail closed. Caller must serialize this
    against every controller command/refresh; this function grants no lease.
    """
    try:
        state = controller.state
        mode_stopped = getattr(getattr(state, "mode", None), "value", None) == "STOPPED"
        owned = controller.owned_url
        player = controller.player
        server = controller.server
        # Observe the real child process as well as the reader indicator.
        stream_alive = server.alive()
        reader_active = server.reader_active()
        # A stale reader indicator still fails closed even when the process
        # has exited; never infer reader inactivity from alive() alone.
        reader_flag = getattr(server, "_reader_active", None)
        joined = controller._joined
        uncertain = controller._group_uncertain
        recovery_url = controller._recovery_url
        group_coord = controller._group_coord
        group_members = controller._group_members
        no_owned_url = (owned is None and player is None)
        no_stream = _is_false(stream_alive) and getattr(server, "proc", object()) is None
        no_reader = _is_false(reader_active) and _is_false(reader_flag)
        no_joined_group = _is_empty(joined, dict)
        no_uncertain_group = _is_false(uncertain)
        no_group_recovery = (
            recovery_url is None and group_coord is None
            and _is_empty(group_members, frozenset)
        )
        # The admission gate accounts for all queued/running requests and
        # refreshes. This controller-level sample cannot verify that alone.
        no_pending_external_activity = False
    except (AttributeError, RuntimeError, OSError, ValueError, TypeError):
        return IdleProof(False, False, False, False, False, False, False, False)

    return IdleProof(
        mode_stopped=type(mode_stopped) is bool and mode_stopped,
        no_owned_url=no_owned_url,
        no_stream=no_stream,
        no_reader=no_reader,
        no_joined_group=no_joined_group,
        no_uncertain_group=no_uncertain_group,
        no_group_recovery=no_group_recovery,
        no_pending_external_activity=no_pending_external_activity,
    )
