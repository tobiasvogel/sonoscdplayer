"""Volatile, single-target selection for the existing player service.

Selection is NOT ownership and grants no transport command by itself.
All methods are invoked by the player service's serialized control worker.
"""
from player_state import Mode


class SelectedTarget:
    def __init__(self):
        self._uid = None

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
        controller.verify(uid)
        self._uid = uid

    def clear(self, controller):
        if controller.state.mode != Mode.STOPPED or controller.owned_url:
            raise ValueError('cannot clear target during active ownership')
        self._uid = None
