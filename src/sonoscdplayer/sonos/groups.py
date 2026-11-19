"""Temporary Sonos groups for playing on several speakers at once.

At PLAY the player joins the selected member speakers to the coordinator;
at STOP it releases exactly that group again. Every step is checked live,
and a member whose release is uncertain is kept in the list, never
forgotten. Choosing speakers in the menu does not call anything here.
"""
from __future__ import annotations
import re
import time

UID_RE = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
MAX_MEMBERS = 15

def normalize_members(coordinator_uid, members):
    """Validate member UIDs and return them as a frozenset.

    Raises:
        ValueError: not a collection, an invalid UID, the coordinator itself
            as member, or more than MAX_MEMBERS members.
    """
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
    """Make SoCo read the current group layout instead of a cached one."""
    state = getattr(player, 'zone_group_state', None)
    clear = getattr(state, 'clear_cache', None)
    if callable(clear):
        clear()

def is_expected_group(player, coordinator_uid, members=frozenset()):
    """True if the player's group is exactly the coordinator plus ``members``."""
    group = player.group
    actual = [p.uid for p in group.members]
    expected = set(members) | {coordinator_uid}
    return (group.coordinator.uid == coordinator_uid and
            len(actual) == len(set(actual)) and set(actual) == expected)

def require_member_ready(player, uid):
    """Check that a member speaker is alone and stopped before joining it.

    Raises:
        RuntimeError: another UID, grouped, or playing.
    """
    if player.uid != uid:
        raise RuntimeError('member identity changed')
    refresh_topology(player)
    if not is_expected_group(player, uid):
        raise RuntimeError('member not standalone')
    if player.get_current_transport_info()['current_transport_state'] != 'STOPPED':
        raise RuntimeError('member has active transport')

def find_speakers(uids, *, discover=None, timeout=5):
    """Find each UID exactly once; return {uid: speaker}.

    Raises:
        RuntimeError: a UID was not found, or found more than once.
    """
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
    """Members whose release did not succeed, from release()'s outcome."""
    return {uid: p for uid, p in joined.items()
            if outcome.get(uid) not in ('released', 'already left')}

class GroupRollbackIncomplete(RuntimeError):
    """Forming a group failed and not every joined member could be released.

    ``remaining`` holds those members; they need a manual check in the app.
    """

    def __init__(self, remaining):
        super().__init__('temporary group rollback incomplete; manual recovery required')
        self.remaining = dict(remaining)

def release(coordinator, joined, *, owned_url=None):
    """Release the members the player joined, if the group is still ours.

    Args:
        coordinator: the group coordinator.
        joined: {uid: speaker} of the members the player joined.
        owned_url: if given, release only while the coordinator still plays it.

    Returns:
        {uid: outcome}: 'released', 'already left' or a 'kept: …' reason.
    """
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
            if player.uid != uid or uid == coordinator.uid:
                outcome[uid] = 'kept: member identity changed'
                continue
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
    """Join ``members`` ({uid: speaker}) to the coordinator; return them.

    Coordinator and members must be alone and stopped. If the group does not
    form within ``settle`` seconds, the joined members are released again.

    Raises:
        RuntimeError: a precondition failed or the group did not form.
        GroupRollbackIncomplete: releasing after a failure was incomplete.
    """
    joined = {}
    try:
        if not is_expected_group(coordinator, coordinator.uid):
            raise RuntimeError('coordinator not standalone')
        if coordinator.get_current_transport_info()['current_transport_state'] != 'STOPPED':
            raise RuntimeError('coordinator transport active')
        for uid, player in members.items():
            require_member_ready(player, uid)
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

def inspect_recovery(coordinator, joined):
    """Read-only, conservative inspection of tracked members.

    No UID is returned to clients. A standalone member is already resolved;
    an exact residual group may be eligible for separately confirmed release.
    """
    if not joined:
        return {'state': 'clear', 'standalone': (), 'attached': (), 'blocked': ()}
    standalone, attached, blocked = [], [], []
    try:
        if coordinator is None:
            raise RuntimeError('missing coordinator')
        refresh_topology(coordinator)
        group = coordinator.group
        actual = [p.uid for p in group.members]
        exact = (group.coordinator.uid == coordinator.uid and
                 len(actual) == len(set(actual)) and
                 set(actual).issubset({coordinator.uid, *joined}))
    except Exception:
        exact = False
    for uid, player in joined.items():
        try:
            if player.uid != uid:
                raise RuntimeError('member identity changed')
            refresh_topology(player)
            if is_expected_group(player, uid):
                standalone.append(uid)
            elif exact and player.group.coordinator.uid == coordinator.uid:
                attached.append(uid)
            else:
                blocked.append(uid)
        except Exception:
            blocked.append(uid)
    # The coordinator's exact group may omit already-standalone tracked
    # members. Check its complete membership again against attached members.
    if attached and not blocked:
        try:
            refresh_topology(coordinator)
            if not is_expected_group(coordinator, coordinator.uid, attached):
                blocked.extend(attached)
                attached.clear()
        except Exception:
            blocked.extend(attached)
            attached.clear()
    state = ('blocked' if blocked else 'attached' if attached else 'clear')
    return {'state': state, 'standalone': tuple(standalone),
            'attached': tuple(attached), 'blocked': tuple(blocked)}
