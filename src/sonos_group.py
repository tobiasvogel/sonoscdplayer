"""Temporary Sonos groups. Selection alone never invokes these functions.

Group mutations require an explicit future player action and live checks.
Never forget members whose release remains uncertain.
"""
from __future__ import annotations
import re
import time

UID_RE = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
MAX_MEMBERS = 15

def normalize_members(coordinator_uid, members):
    if members is None:
        return frozenset()
    if isinstance(members, (str, bytes)) or not isinstance(members, (list, tuple, set, frozenset)):
        raise ValueError('members must be a collection of UIDs')
    result = set()
    for uid in members:
        if not isinstance(uid, str) or not UID_RE.fullmatch(uid) or uid == coordinator_uid:
            raise ValueError('invalid member UID')
        result.add(uid)
    if len(result) > MAX_MEMBERS:
        raise ValueError('too many members')
    return frozenset(result)

def refresh_topology(player):
    state = getattr(player, 'zone_group_state', None)
    clear = getattr(state, 'clear_cache', None)
    if callable(clear):
        clear()

def is_expected_group(player, coordinator_uid, members=frozenset()):
    group = player.group
    actual = [p.uid for p in group.members]
    expected = set(members) | {coordinator_uid}
    return (group.coordinator.uid == coordinator_uid and
            len(actual) == len(set(actual)) and set(actual) == expected)

def check_member(player, uid):
    if player.uid != uid:
        raise RuntimeError('member identity changed')
    refresh_topology(player)
    if not is_expected_group(player, uid):
        raise RuntimeError('member not standalone')
    if player.get_current_transport_info()['current_transport_state'] != 'STOPPED':
        raise RuntimeError('member has active transport')

def find_players(uids, *, discover=None, timeout=5):
    if discover is None:
        from soco import discover
    found = {}
    devices = discover(timeout=timeout) or ()
    for uid in uids:
        matches = [p for p in devices if p.uid == uid]
        if len(matches) != 1:
            raise RuntimeError('member not discovered uniquely')
        found[uid] = matches[0]
    return found

def unresolved_members(joined, outcome):
    return {uid: p for uid, p in joined.items()
            if outcome.get(uid) not in ('released', 'already left')}

class GroupRollbackIncomplete(RuntimeError):
    def __init__(self, remaining):
        super().__init__('temporary group rollback incomplete; manual recovery required')
        self.remaining = dict(remaining)

def release(coordinator, joined, *, owned_url=None):
    """Undo only an exact group we created; preserve unknown outcomes."""
    if not joined:
        return {}
    try:
        refresh_topology(coordinator)
        if not is_expected_group(coordinator, coordinator.uid, joined):
            return {uid:'kept: topology changed' for uid in joined}
        if (owned_url is not None and
                coordinator.get_current_track_info().get('uri') != owned_url):
            return {uid:'kept: foreign playback' for uid in joined}
    except Exception:
        return {uid:'kept: verification failed' for uid in joined}
    outcome = {}
    for uid, player in joined.items():
        try:
            refresh_topology(player)
            expected = {coordinator.uid} | {
                k for k in joined if outcome.get(k) != 'released'
            }
            group = player.group
            actual = [p.uid for p in group.members]
            if (group.coordinator.uid != coordinator.uid or
                    len(actual) != len(set(actual)) or set(actual) != expected):
                outcome[uid] = 'kept: topology changed'
                continue
            if (owned_url is not None and
                    coordinator.get_current_track_info().get('uri') != owned_url):
                outcome[uid] = 'kept: foreign playback'
                continue
            player.unjoin()
            refresh_topology(player)
            outcome[uid] = ('released' if is_expected_group(player, uid)
                            else 'kept: unjoin unverified')
        except Exception:
            outcome[uid] = 'kept: unjoin failed'
    return outcome

def form(coordinator, members, *, settle=3.0, sleep=time.sleep, clock=time.monotonic):
    """Only caller-authorized joins; rollback records incomplete cleanup."""
    joined = {}
    try:
        if not is_expected_group(coordinator, coordinator.uid):
            raise RuntimeError('coordinator not standalone')
        if coordinator.get_current_transport_info()['current_transport_state'] != 'STOPPED':
            raise RuntimeError('coordinator transport active')
        for uid, player in members.items():
            check_member(player, uid)
        for uid, player in members.items():
            joined[uid] = player
            player.join(coordinator)
        deadline = clock()+settle
        while True:
            refresh_topology(coordinator)
            if is_expected_group(coordinator, coordinator.uid, joined):
                return joined
            if clock() >= deadline:
                raise RuntimeError('temporary group did not form')
            sleep(0.25)
    except BaseException as exc:
        remaining = unresolved_members(joined, release(coordinator, joined))
        if remaining:
            raise GroupRollbackIncomplete(remaining) from exc
        raise
