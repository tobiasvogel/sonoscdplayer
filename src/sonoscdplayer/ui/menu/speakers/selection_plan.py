"""Pure checkbox selection planning; no additional state file or IPC."""
from dataclasses import dataclass
from sonoscdplayer.sonos.groups import normalize_members, UID_RE

@dataclass(frozen=True)
class GroupIntent:
    """Coordinator and members to send to the player as new selection."""
    coordinator: str
    members: tuple[str, ...]

def plan_from_applied(targets, applied):
    """Turn the checked speakers into a GroupIntent (first one coordinates).

    Whether a grouped speaker may be used is decided by the player, which
    checks the speakers live (and splits a leftover group of its own).

    Raises:
        ValueError: nothing checked, or an invalid UID.
    """
    ordered = [t for t in targets if t.key in applied]
    if not ordered or len(ordered) != len(applied):
        raise ValueError('empty or missing selection')
    if len({t.key for t in ordered}) != len(ordered):
        raise ValueError('duplicate speaker')
    if any(not UID_RE.fullmatch(t.key or '') for t in ordered):
        raise ValueError('invalid UID')
    coordinator = ordered[0].key
    return GroupIntent(coordinator, tuple(sorted(normalize_members(
        coordinator, [t.key for t in ordered[1:]]))))
