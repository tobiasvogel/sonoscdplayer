"""Volatile, single-target selection for the existing player service.

Selection is NOT ownership and grants no transport command by itself.
All methods are invoked by the player service's serialized control worker.
"""
from player_state import Mode


def verify_solo_stopped(uid):
    from sonos_parallel_test import find_target
    player = find_target(uid)
    if player.uid != uid:
        raise RuntimeError('target identity mismatch')
    group = player.group
    members = list(group.members)
    if len(members) != 1 or members[0].uid != uid or group.coordinator.uid != uid:
        raise RuntimeError('target is not solo coordinator')
    if player.get_current_transport_info()['current_transport_state'] != 'STOPPED':
        raise RuntimeError('target is not stopped')
    return True


class SelectedTarget:
    def __init__(self, verify=verify_solo_stopped):
        self._uid = None
        self._verify = verify

    def summary(self):
        # Do not disclose a private Sonos UID through status/read-only RPC.
        return {'selected': self._uid is not None, 'ready_for_playback': False}

    def uid_for_explicit_action(self):
        return self._uid

    def set(self, controller, uid):
        if controller.state.mode != Mode.STOPPED or controller.owned_url:
            raise ValueError('target selection requires stopped, unowned player')
        # Reuse the same live verification performed before existing PLAY.
        # Validates STOPPED solo coordinator and does not send Sonos writes.
        self._verify(uid)
        self._uid = uid

    def clear(self, controller):
        if controller.state.mode != Mode.STOPPED or controller.owned_url:
            raise ValueError('cannot clear target during active ownership')
        self._uid = None
