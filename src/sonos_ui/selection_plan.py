"""Pure checkbox selection planning; no additional state file or IPC."""
from dataclasses import dataclass
from sonos_group import normalize_members, UID_RE

@dataclass(frozen=True)
class GroupIntent:
    coordinator: str
    members: tuple[str, ...]

def plan_from_applied(targets, applied):
    ordered = [t for t in targets if t.key in applied]
    if not ordered or len(ordered) != len(applied):
        raise ValueError('empty or missing selection')
    if len({t.key for t in ordered}) != len(ordered):
        raise ValueError('duplicate speaker')
    if any(not UID_RE.fullmatch(t.key or '') for t in ordered):
        raise ValueError('invalid UID')
    if any(tuple(t.members) != (t.key,) for t in ordered):
        raise ValueError('existing Sonos group must not be changed')
    coordinator = ordered[0].key
    return GroupIntent(coordinator, tuple(sorted(normalize_members(
        coordinator, [t.key for t in ordered[1:]]))))
