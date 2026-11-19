"""The speakers chosen in the menu: one coordinator plus optional members.

Choosing speakers is not playing on them: the selection sends no command to
Sonos. With a TargetStore the selection survives restarts; a restored
selection is checked against Sonos only at the next PLAY.
All methods are called by the player service's serialized control worker.
"""
from sonoscdplayer.player.state import Mode
from sonoscdplayer.sonos.groups import normalize_members, find_speakers, require_member_ready


def _find_solo_stopped_speaker(uid):
    # Looked up at call time, so that tests can replace find_speaker.
    from sonoscdplayer.sonos.speaker_checks import find_solo_stopped_speaker
    return find_solo_stopped_speaker(uid)


class SelectedTarget:
    """The current speaker selection of the player.

    Args:
        verify: checks the coordinator before it is selected (alone, stopped).
        find_many: finds the member speakers by UID.
        store: optional TargetStore that keeps the selection across restarts.
    """

    def __init__(self, verify=_find_solo_stopped_speaker, find_many=find_speakers, store=None):
        self._uid = None
        self._members = ()
        self._verify = verify
        self._find_many = find_many
        self._store = store

    def restore(self):
        """Load the remembered selection without contacting any speaker."""
        stored = self._store.load() if self._store is not None else None
        if stored is None:
            return False
        self._uid, self._members = stored
        return True

    def summary(self):
        """Selection state for status replies, without any Sonos UID."""
        return {'selected': self._uid is not None, 'ready_for_playback': False}

    def speakers(self):
        """All selected UIDs (coordinator first) for get-target only.

        The control socket is local and private to the service account; the
        OLED menu needs them to show the remembered selection as checkmarks.
        """
        return [] if self._uid is None else [self._uid, *self._members]

    def coordinator_uid(self):
        """UID of the selected coordinator, or None if nothing is selected."""
        return self._uid

    def member_uids(self):
        """UIDs of the selected group members (may be empty)."""
        return self._members

    def set(self, controller, uid, members=None):
        """Select a coordinator and members after checking them live.

        Allowed only while the player is stopped and plays nothing of its own.

        Raises:
            ValueError: the player is busy, or a member UID is invalid.
            RuntimeError: a speaker is missing, grouped or not stopped.
        """
        if controller.state.mode != Mode.STOPPED or controller.owned_url:
            raise ValueError('target selection requires stopped, unowned player')
        # Reuse the same live verification performed before existing PLAY.
        # Validates STOPPED solo coordinator and does not send Sonos writes.
        selected_members = tuple(sorted(normalize_members(uid, members)))
        self._verify(uid)
        if selected_members:
            discovered = self._find_many(selected_members)
            if set(discovered) != set(selected_members):
                raise RuntimeError('incomplete member discovery')
            for member_uid in selected_members:
                require_member_ready(discovered[member_uid], member_uid)
        self._uid = uid
        self._members = selected_members
        if self._store is not None:
            self._store.save(uid, selected_members)

    def clear(self, controller):
        """Forget the selection (only while the player is stopped)."""
        if controller.state.mode != Mode.STOPPED or controller.owned_url:
            raise ValueError('cannot clear target during active ownership')
        self._uid = None
        self._members = ()
        if self._store is not None:
            self._store.clear()
