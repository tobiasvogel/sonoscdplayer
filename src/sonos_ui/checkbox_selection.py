"""UID-based checkbox draft for explicit solo or temporary-group selection.

No Sonos discovery, transport commands, playback, or persistent side effects.
The caller must separately confirm the draft and invoke the player RPC.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class SpeakerChoice:
    uid: str
    name: str
    solo: bool
    stopped: bool

@dataclass(frozen=True)
class TargetChoice:
    coordinator_uid: str
    member_uids: tuple[str, ...]

    @property
    def grouped(self):
        return bool(self.member_uids)


class CheckboxSelection:
    """Keep focus, checkbox order, and last acknowledged target independent.

    The first *checked* UID is the coordinator; discovery reorder does not
    change it. An unavailable UID is retained as a draft item, never silently
    replaced with another speaker or submitted to the player.
    """
    def __init__(self):
        self._checked = ()
        self._confirmed = None
        self._last_preview = None

    @property
    def checked_uids(self):
        return self._checked

    @property
    def confirmed(self):
        return self._confirmed

    def toggle(self, uid):
        if not isinstance(uid, str) or not uid:
            raise ValueError('nonempty speaker UID required')
        self._last_preview = None
        if uid in self._checked:
            self._checked = tuple(x for x in self._checked if x != uid)
        else:
            self._checked += (uid,)
        return self._checked

    def begin(self, *, preserve_confirmed=True):
        """Opening the menu may restore the last acknowledged checkbox state."""
        self._last_preview = None
        if preserve_confirmed and self._confirmed is not None:
            self._checked = (self._confirmed.coordinator_uid,
                             *self._confirmed.member_uids)
        else:
            self._checked = ()

    def cancel(self):
        """Discard the draft; do not clear the player target or mutate Sonos."""
        self.begin(preserve_confirmed=True)

    def preview(self, available):
        """Return an explicit plan only for currently eligible unique UIDs.

        available is the caller's recent read-only discovery snapshot.
        The player re-verifies all devices at the eventual explicit PLAY.
        """
        if not self._checked:
            raise ValueError('at least one speaker must be checked')
        by_uid = {}
        for item in available:
            if not isinstance(item, SpeakerChoice) or not item.uid:
                raise ValueError('invalid discovery entry')
            if item.uid in by_uid:
                raise ValueError('ambiguous duplicate speaker UID')
            by_uid[item.uid] = item
        for uid in self._checked:
            speaker = by_uid.get(uid)
            if speaker is None:
                raise RuntimeError('selected speaker no longer available')
            if not speaker.solo or not speaker.stopped:
                raise RuntimeError('selected speaker is grouped or active')
        self._last_preview = TargetChoice(self._checked[0], self._checked[1:])
        return self._last_preview

    def acknowledge(self, plan):
        """Call ONLY after the application worker receives positive RPC ACK.

        A failed or unacknowledged RPC leaves confirmed unchanged.
        """
        if not isinstance(plan, TargetChoice):
            raise ValueError('invalid selection confirmation')
        if not self._checked or self._last_preview != plan or plan != TargetChoice(self._checked[0], self._checked[1:]):
            raise RuntimeError('checkbox selection changed before acknowledgment')
        self._confirmed = plan
