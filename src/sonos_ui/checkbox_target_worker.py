"""Explicit checkbox confirmation bridge to the existing player set-target RPC.

Must be invoked by the application's explicit 'apply selection' worker action.
Never starts CD playback, forms groups, or writes Sonos directly.
"""
from .checkbox_selection import TargetChoice
from .target_rpc_client import submit_target


def apply_selection(session, plan, *, socket_path, submit=submit_target):
    """Keep last acknowledged UI target untouched if the player rejects RPC."""
    if not isinstance(plan, TargetChoice):
        raise ValueError('validated selection plan required')
    if session.checkbox_selection is None:
        raise RuntimeError('checkbox selection inactive')
    # Compare with the most recent, still-current preview before sending.
    if session.checkbox_selection.preview(session.speaker_choices) != plan:
        raise RuntimeError('selection changed before submission')
    acknowledged = submit(plan.coordinator_uid,
                          members=plan.member_uids,
                          socket_path=socket_path)
    if acknowledged is not True:
        raise RuntimeError('player did not acknowledge selection')
    # No menu closing occurs here: caller supplies the real UI clock when
    # invoking session.acknowledge_selection(plan, now_ms=...).
    return True
