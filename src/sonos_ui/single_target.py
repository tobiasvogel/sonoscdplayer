"""M0.17a phase 1: pure single-speaker intent validation.

Never issues player commands, changes topology, stores credentials or infers
authorization from a UI checkbox. A later explicit player-command boundary
must revalidate live Sonos state before any transport action.
"""
from dataclasses import dataclass
from .multi_target import MultiSelection


@dataclass(frozen=True)
class TargetIntent:
    """An ephemeral selection, not player ownership or permission to play."""
    uid: str
    display_name: str


class TargetSelectionError(ValueError):
    pass


def select_one(state: MultiSelection) -> TargetIntent:
    """Build a selection only from one locally confirmed, current solo speaker."""
    if not isinstance(state, MultiSelection):
        raise TypeError('expected MultiSelection')
    if state.topology.error:
        raise TargetSelectionError('incomplete speaker topology')
    if state.reviewing or state.review_valid or state.draft != state.applied:
        raise TargetSelectionError('selection not confirmed')
    if len(state.applied) != 1:
        raise TargetSelectionError('exactly one speaker required')
    uid = next(iter(state.applied))
    matches = [t for t in state.topology.targets if t.key == uid]
    if len(matches) != 1:
        raise TargetSelectionError('speaker missing from current topology')
    target = matches[0]
    # Never auto-unjoin, target the group coordinator, or attach to groups.
    if target.members != (uid,):
        raise TargetSelectionError('speaker is in a Sonos group')
    return TargetIntent(uid=uid, display_name=target.label)
