"""INP-02: relative Sonos volume for the physical volume buttons.

Runs only inside the player service's serialized control worker. Two cases are
allowed (decision D-213):

1. Own playback (PLAYING or PAUSED, owned stream): the live ownership gate of
   PAUSE/RESUME is applied, then the group volume of the player's own (possibly
   temporary) group is changed, like the group slider of the Sonos app.
2. STOPPED with a confirmed target selection: only if no selected speaker is
   currently playing anything. The speakers are not grouped yet, so each one is
   changed individually. The "nothing is playing" check is cached for a few
   seconds; a short gap between check and change is accepted.

Every other state is refused. No grouping, no transport commands, no retries.
"""
from __future__ import annotations

import time

from player_state import Mode
from sonos_group import find_players

MAX_STEP = 10            # largest relative change per request
IDLE_CHECK_SECONDS = 5.0  # reuse of the "nothing is playing" check
BUSY_TRANSPORTS = frozenset({'PLAYING', 'TRANSITIONING'})


def parse_volume_command(command):
    """'volume +3' -> 3, 'volume -1' -> -1; None if not a volume command."""
    parts = command.split()
    if len(parts) != 2 or parts[0] != 'volume':
        return None
    value = parts[1]
    if len(value) < 2 or value[0] not in '+-' or not value[1:].isdigit():
        raise ValueError('volume step must be +N or -N')
    step = int(value)
    if not 1 <= abs(step) <= MAX_STEP:
        raise ValueError(f'volume step must be 1..{MAX_STEP}')
    return step


def _relative(player, step):
    """SoCo's set_relative_volume returns the new volume (0..100)."""
    return int(player.set_relative_volume(step))


def _group_relative(coordinator, step):
    group = coordinator.group
    setter = getattr(group, 'set_relative_volume', None)
    if setter is not None:
        return int(setter(step))
    # Older SoCo without group volume: change each member relatively.
    level = None
    for member in group.members:
        new = _relative(member, step)
        if member.uid == coordinator.uid:
            level = new
    return level if level is not None else int(coordinator.volume)


class VolumeControl:
    def __init__(self, *, find_many=find_players, clock=time.monotonic,
                 idle_check_seconds=IDLE_CHECK_SECONDS):
        self._find_many = find_many
        self._clock = clock
        self._idle_check_seconds = idle_check_seconds
        self._players_key = None
        self._players = None
        self._idle_until = 0.0

    def adjust(self, controller, selection, step):
        mode = controller.state.mode
        if mode in (Mode.PLAYING, Mode.PAUSED):
            return self._adjust_owned(controller, step)
        if mode == Mode.STOPPED and not controller.owned_url:
            return self._adjust_stopped(selection, step)
        raise ValueError('volume requires own playback or a stopped, selected target')

    def _adjust_owned(self, controller, step):
        if not controller.seen_playing or not controller.owned_url or not controller.server.alive():
            raise ValueError('volume requires confirmed owned playback')
        expected = 'PLAYING' if controller.state.mode == Mode.PLAYING else 'PAUSED_PLAYBACK'
        player = controller.verify_existing()
        controller._ownership(player, expected)
        return _group_relative(player, step)

    def _selected_players(self, selection):
        uid = selection.uid_for_explicit_action() if selection is not None else None
        if uid is None:
            raise ValueError('no confirmed target')
        uids = (uid, *selection.members_for_explicit_action())
        if uids != self._players_key:
            found = self._find_many(uids)
            if set(found) != set(uids):
                raise RuntimeError('selected speakers not discovered')
            self._players_key, self._players = uids, found
            self._idle_until = 0.0
        return uid, self._players

    def _adjust_stopped(self, selection, step):
        uid, players = self._selected_players(selection)
        now = self._clock()
        if now >= self._idle_until:
            for member_uid, player in players.items():
                if player.uid != member_uid:
                    raise RuntimeError('speaker identity changed')
                transport = (player.group.coordinator.get_current_transport_info()
                             ['current_transport_state'])
                if transport in BUSY_TRANSPORTS:
                    self._idle_until = 0.0
                    raise RuntimeError('a selected speaker is playing; volume left unchanged')
            self._idle_until = now + self._idle_check_seconds
        level = None
        for member_uid, player in players.items():
            new = _relative(player, step)
            if member_uid == uid:
                level = new
        return level

    def forget(self):
        """Drop cached speakers (e.g. after the selection changed)."""
        self._players_key = None
        self._players = None
        self._idle_until = 0.0
