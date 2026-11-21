"""The speakers chosen in the menu: one coordinator plus optional members.

Choosing speakers is not playing on them: the selection sends no command to
Sonos, except to split a stopped leftover group made only of the player's own
selected speakers. With a TargetStore the selection survives restarts; a restored
selection is checked against Sonos only at the next PLAY.
All methods are called by the player service's serialized control worker.
"""
from sonoscdplayer.player.state import Mode
from sonoscdplayer.sonos.groups import normalize_members, find_speakers, require_member_ready_releasing, make_alone
from sonoscdplayer.sonos.speaker_checks import SpeakerGrouped


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

        While the player is stopped the speakers are checked; during own
        playback the controller changes the running playback (see
        Controller.retarget()).

        Raises:
            ValueError: the player is busy, or a member UID is invalid.
            RuntimeError: a speaker is missing, grouped or not stopped.
        """
        selected_members = tuple(sorted(normalize_members(uid, members)))
        if controller.state.mode in (Mode.PLAYING, Mode.PAUSED) and controller.owned_url:
            # During playback the controller changes the speakers itself.
            # The playing coordinator stays coordinator while it is still
            # chosen, so adding a speaker never restarts the track.
            chosen = {uid, *selected_members}
            if controller.uid in chosen and controller.uid != uid:
                uid = controller.uid
                selected_members = tuple(sorted(chosen - {uid}))
            controller.retarget(uid, selected_members)
            self._remember(uid, selected_members)
            return
        if controller.state.mode != Mode.STOPPED or controller.owned_url:
            raise ValueError('speaker change not possible while playback is starting')
        # Reuse the same live verification performed before existing PLAY.
        # Only a group the player remembers forming (or taking over) counts
        # as its own; matching UIDs of a selection are no proof. Any other
        # idle group only loses the chosen speakers (make_alone).
        remembered = getattr(controller, 'remembered_group', None)
        own = set(remembered()) if callable(remembered) else set()
        try:
            self._verify(uid)
        except SpeakerGrouped as exc:
            if not make_alone(exc.speaker, own):
                raise
            forget = getattr(controller, 'forget_leftover_group', None)
            if callable(forget):
                forget()
            self._verify(uid)
        if selected_members:
            discovered = self._find_many(selected_members)
            if set(discovered) != set(selected_members):
                raise RuntimeError('incomplete member discovery')
            for member_uid in selected_members:
                require_member_ready_releasing(discovered[member_uid], member_uid, own)
        self._remember(uid, selected_members)

    def follow(self, uid, members):
        """Take over a group change made in the Sonos app during playback."""
        self._remember(uid, tuple(sorted(members)))

    def _remember(self, uid, members):
        self._uid = uid
        self._members = members
        if self._store is not None:
            self._store.save(uid, members)

    def clear(self, controller):
        """Forget the selection (only while the player is stopped)."""
        if controller.state.mode != Mode.STOPPED or controller.owned_url:
            raise ValueError('cannot clear target during active ownership')
        self._uid = None
        self._members = ()
        if self._store is not None:
            self._store.clear()
