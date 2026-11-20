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

def wait_standalone(player, uid, *, settle=3.0, sleep=time.sleep, clock=time.monotonic):
    """Wait until Sonos reports ``player`` alone in its group; False on timeout."""
    deadline = clock() + settle
    while True:
        refresh_topology(player)
        if is_expected_group(player, uid):
            return True
        if clock() >= deadline:
            return False
        sleep(0.25)


def release_leftover(speaker, own_uids, *, sleep=time.sleep, clock=time.monotonic):
    """Split a stopped group that consists only of the player's own speakers.

    After an interrupted playback a group of the selection can stay behind
    (for example when the player was restarted before releasing it). The
    group of ``speaker`` is split only if every speaker in it is in
    ``own_uids`` (the old or new selection) and its coordinator is stopped;
    any other group is left alone.

    Returns:
        True if ``speaker`` is alone in its group afterwards.
    """
    refresh_topology(speaker)
    group = speaker.group
    coordinator = group.coordinator
    members = list(group.members)
    if len(members) < 2:
        return False
    foreign = len({p.uid for p in members} - set(own_uids))
    if foreign:
        print(f'[sonos] group of {len(members)} speakers left alone: '
              f'{foreign} of them not ours', flush=True)
        return False
    transport = coordinator.get_current_transport_info()['current_transport_state']
    if transport != 'STOPPED':
        print(f'[sonos] group of {len(members)} own speakers left alone: '
              f'coordinator is {transport}', flush=True)
        return False
    others = [p for p in members if p.uid != coordinator.uid]
    for member in others:
        member.unjoin()
    for member in others:
        wait_standalone(member, member.uid, sleep=sleep, clock=clock)
    alone = wait_standalone(speaker, speaker.uid, sleep=sleep, clock=clock)
    print(f'[sonos] split a leftover group of {len(members)} own speakers; '
          f'{"done" if alone else "not confirmed"}', flush=True)
    return alone


def require_member_ready_releasing(player, uid, own_uids):
    """require_member_ready(); a leftover group of own speakers is split first."""
    try:
        require_member_ready(player, uid)
    except RuntimeError as exc:
        if str(exc) != 'member not standalone' or not release_leftover(player, own_uids):
            raise
        require_member_ready(player, uid)


def change_members(coordinator, joined, wanted, find_many, *, sleep=time.sleep,
                   clock=time.monotonic, settle=3.0):
    """Add and remove members of a playing group without interrupting it.

    Args:
        coordinator: the group coordinator (plays our stream).
        joined: {uid: speaker} of the current members.
        wanted: UIDs of the members the group should have.
        find_many: finds speakers by UID (for new members).

    Returns:
        {uid: speaker} of the members after the change.

    Raises:
        RuntimeError: a new member is not ready (grouped elsewhere, playing,
            not found), or the group did not settle in time.
    """
    wanted = frozenset(wanted)
    joined = dict(joined)
    own = {coordinator.uid, *wanted, *joined}
    added = {}
    if wanted - set(joined):
        added = find_many(sorted(wanted - set(joined)))
        if set(added) != wanted - set(joined):
            raise RuntimeError('member discovery incomplete')
        for uid, member in added.items():
            require_member_ready_releasing(member, uid, own)
    for uid in sorted(set(joined) - wanted):
        joined.pop(uid).unjoin()
    for uid, member in added.items():
        member.join(coordinator)
        joined[uid] = member
    deadline = clock() + settle
    while True:
        refresh_topology(coordinator)
        if is_expected_group(coordinator, coordinator.uid, joined):
            return joined
        if clock() >= deadline:
            raise RuntimeError('changed group did not settle')
        sleep(0.25)


def release(coordinator, joined, *, owned_url=None, sleep=time.sleep, clock=time.monotonic):
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
            # Sonos reports a member's unjoin to the others with a delay, so
            # a member released just before may still be listed here.
            group = player.group
            actual = [p.uid for p in group.members]
            if (group.coordinator.uid != coordinator.uid or
                    len(actual) != len(set(actual)) or
                    not {coordinator.uid, uid} <= set(actual) <= {coordinator.uid, *joined}):
                outcome[uid] = 'kept: topology changed'
                continue
            if (owned_url is not None and
                    coordinator.get_current_track_info().get('uri') != owned_url):
                outcome[uid] = 'kept: foreign playback'
                continue
            player.unjoin()
            outcome[uid] = ('released' if wait_standalone(player, uid, sleep=sleep, clock=clock)
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
        remaining = unresolved_members(joined, release(coordinator, joined, sleep=sleep, clock=clock))
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
