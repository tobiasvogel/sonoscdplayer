"""M0.16a: read-only SoCo discovery snapshots; no Sonos transport writes.

UIDs stay in memory, not in CLI output or preview filenames. A stable key
maps a visible name to one exact device only within the current snapshot.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import Counter
import time

@dataclass(frozen=True)
class Speaker:
    uid: str
    name: str

@dataclass(frozen=True)
class DiscoverySnapshot:
    speakers: tuple[Speaker, ...]
    searching: bool = False
    error: str | None = None

    @property
    def names(self) -> tuple[str, ...]:
        """Disambiguate duplicate names without displaying private identifiers."""
        counts = Counter(item.name for item in self.speakers)
        seen: dict[str, int] = {}
        values = []
        for item in self.speakers:
            seen[item.name] = seen.get(item.name, 0) + 1
            values.append(item.name if counts[item.name] == 1 else f'{item.name} ({seen[item.name]})')
        return tuple(values)

    def resolve(self, index: int, *, expected_name: str | None = None) -> Speaker | None:
        """Resolve an intent only in this snapshot; caller must revalidate live."""
        if type(index) is not int or not 0 <= index < len(self.speakers):
            return None
        if expected_name is not None and expected_name != self.names[index]:
            return None
        return self.speakers[index]


def from_devices(devices) -> DiscoverySnapshot:
    """Snapshot SoCo objects immediately; no objects escape to the UI."""
    by_uid = {}
    for device in devices or ():
        try:
            uid = device.uid
            name = device.player_name
        except (AttributeError, OSError, ValueError):
            continue
        if not isinstance(uid, str) or not uid.strip():
            continue
        if not isinstance(name, str) or not name.strip():
            name = 'Unbenannter Lautsprecher'
        by_uid[uid] = Speaker(uid=uid, name=name.strip())
    ordered = sorted(by_uid.values(), key=lambda item: (item.name.casefold(), item.uid))
    return DiscoverySnapshot(tuple(ordered))


def discover_speakers(*, timeout=3.0, discover_fn=None) -> DiscoverySnapshot:
    """Use existing SoCo discovery only; bounded attempt, no writes to speakers."""
    if not 0 < timeout <= 10:
        raise ValueError('timeout must be >0 and <=10 seconds')
    if discover_fn is None:
        from soco import discover
        discover_fn = discover
    try:
        return from_devices(discover_fn(timeout=timeout))
    except Exception as exc:
        return DiscoverySnapshot((), error=type(exc).__name__)
