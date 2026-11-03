"""Read-only Sonos group topology and local checkbox selection.

Never modifies Sonos groups, transports, or persistent player configuration.
An observed topology is *not* an authorization to play; future writes require
new live ownership and topology checks in the player service.
"""
from __future__ import annotations
from dataclasses import dataclass, replace

@dataclass(frozen=True)
class Target:
    key: str
    label: str
    members: tuple[str, ...]
    member_names: tuple[str, ...]

@dataclass(frozen=True)
class Topology:
    targets: tuple[Target, ...]
    error: str | None = None

    def keys(self):
        return frozenset(target.key for target in self.targets)


def from_devices(devices):
    """List individual discovered speakers, as in M0.16a.

    Existing Sonos group membership is read-only metadata. Never collapse
    speakers into a synthetic "+N" entry. Nor does an individual checkbox
    authorize independent transport commands to a grouped member.
    """
    entries = {}
    errors = 0
    for device in devices or ():
        try:
            uid, name = device.uid, device.player_name
            if not isinstance(uid, str) or not uid or not isinstance(name, str):
                raise ValueError('invalid speaker')
            group = device.group
            coordinator = group.coordinator
            members = tuple(group.members)
            coordinator_uid = coordinator.uid
            if not isinstance(coordinator_uid, str) or not coordinator_uid or not members:
                raise ValueError('invalid coordinator')
            info = []
            for member in members:
                member_uid, member_name = member.uid, member.player_name
                if not isinstance(member_uid, str) or not member_uid or not isinstance(member_name, str):
                    raise ValueError('invalid group member')
                info.append((member_uid, member_name.strip() or 'Unbenannter Lautsprecher'))
            if uid not in {member_uid for member_uid, _ in info} or coordinator_uid not in {member_uid for member_uid, _ in info}:
                raise ValueError('inconsistent group')
            info.sort(key=lambda item: (item[1].casefold(), item[0]))
            label = name.strip() or 'Unbenannter Lautsprecher'
            target = Target(uid, label, tuple(member_uid for member_uid, _ in info),
                            tuple(member_name for _, member_name in info))
            if uid in entries and entries[uid] != target:
                return Topology((), 'TopologyChanged')
            entries[uid] = target
        except (AttributeError, ValueError, TypeError, OSError):
            errors += 1
    targets = tuple(sorted(entries.values(), key=lambda t: (t.label.casefold(), t.key)))
    return Topology(targets, 'IncompleteTopology' if errors else None)


def discover_targets(*,timeout=3,discover_fn=None):
    if not 0 < timeout <= 10:
        raise ValueError('timeout must be > 0 and <= 10')
    if discover_fn is None:
        from soco import discover
        discover_fn=discover
    try:
        return from_devices(discover_fn(timeout=timeout))
    except Exception as exc:
        return Topology((),type(exc).__name__)

@dataclass(frozen=True)
class MultiSelection:
    """Checkbox draft and last player-acknowledged target remain separate."""
    topology: Topology = Topology(())
    draft: frozenset[str] = frozenset()
    applied: frozenset[str] = frozenset()
    focus: int = 0
    notice: str = ''

    def update(self,topology):
        keys=topology.keys()
        missing=(self.draft|self.applied)-keys
        # Keep the last acknowledged target even when discovery temporarily loses
        # a speaker. Never silently shrink or replace the player's target.
        return replace(self,topology=topology,
                       focus=min(self.focus,len(topology.targets)+2),
                       notice='status.missing_targets' if missing else self.notice)

    def action(self,action):
        if action not in ('next','previous','confirm','back'):
            raise ValueError('unknown action')
        if action=='back':
            return replace(self,draft=self.applied,notice='')
        n=len(self.topology.targets)
        if action in ('next','previous'):
            return replace(self,focus=max(0,min(n+2,self.focus+(1 if action=='next' else -1))),
                           notice='')
        if self.focus==n+2:
            return replace(self,draft=self.applied,notice='menu.back')
        if self.focus==n+1:
            return replace(self,notice='menu.language')
        if self.focus==n:
            if self.topology.error or not self.draft:
                return replace(self,notice='status.invalid_selection')
            return replace(self,notice='status.apply_requested')
        if not self.topology.error and 0<=self.focus<n:
            key=self.topology.targets[self.focus].key
            draft=self.draft ^ {key}
            return replace(self,draft=frozenset(draft),notice='')
        return self

    def selections(self):
        return tuple(t.label for t in self.topology.targets if t.key in self.draft)
