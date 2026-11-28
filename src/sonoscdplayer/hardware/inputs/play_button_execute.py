"""Opt-in safe PLAY/PAUSE command path.

Read status and selection once per physical press, apply the fail-closed policy,
then attempt exactly one player transport command. No retry, UID, or implicit
fallback to the legacy 'playpause' RPC.

NEXT/PREVIOUS (and STOP, which goes back to the first track) while STOPPED
only move the selected track number in the player, so they do not wake the
drive. NEXT/PREVIOUS do not get lost either: presses that arrive while another
press is in flight are counted and sent afterwards, but only if the player is
still STOPPED then. The real track is read from the CD at PLAY.
"""
import threading

from sonoscdplayer.hardware.inputs.play_button_policy import plan_playpause, plan_stop, plan_track_change, plan_eject
from sonoscdplayer.hardware.inputs.play_button_preview import read_snapshot

SAFE_COMMANDS = frozenset(('play-selected', 'pause', 'resume', 'stop', 'reset', 'next', 'previous', 'eject'))


def _mode(status):
    """Player mode from a status reply, or None."""
    state = status.get('state') if isinstance(status, dict) else None
    player = state.get('player') if isinstance(state, dict) else None
    return player.get('mode') if isinstance(player, dict) else None


def _has_selection(target):
    selection = target.get('target_selection') if isinstance(target, dict) else None
    return isinstance(selection, dict) and selection.get('selected') is True


class SelectedTargetExecutor:
    """Nonblocking one-shot transport worker; at most one press in flight.

    NEXT/PREVIOUS pressed meanwhile are counted, not dropped (see module docstring).
    """

    def __init__(self, socket_path, *, reader=read_snapshot, sender=None,
                 report=print, activity_before_read=False, notify=None):
        if sender is None:
            raise ValueError('a one-shot sender must be supplied')
        self.socket_path = socket_path
        self.reader = reader
        self.sender = sender
        self.report = report
        self.activity_before_read = activity_before_read
        # Optional display feedback: notify('requested' | 'no_target' | 'blocked').
        self.notify = notify
        self._lock = threading.Lock()
        self._busy = False
        self._pending_steps = 0     # NEXT (+1) / PREVIOUS (-1) pressed while busy

    def submit(self, action='playpause'):
        """Carry out one PLAY/PAUSE (or STOP, track change, EJECT) press in the background."""
        if action not in ('playpause', 'stop', 'next', 'previous', 'eject'):
            return False
        with self._lock:
            if self._busy:
                if action in ('next', 'previous'):
                    self._pending_steps += 1 if action == 'next' else -1
                    return True
                return False
            self._busy = True
        if action == 'playpause':
            self._notify('requested')

        def perform():
            try:
                self._perform(action)
            finally:
                self._drain_pending_steps()

        try:
            threading.Thread(target=perform, name='selected-target-execute',
                             daemon=True).start()
        except Exception:
            with self._lock:
                self._busy = False
                self._pending_steps = 0
            raise
        return True

    def _perform(self, action):
        """One press: wake if needed, read status and selection, send one command."""
        sent = False
        try:
            status = None
            if action in ('next', 'previous', 'stop'):
                # A stopped player only moves its track number: no wake needed.
                status = self.reader(self.socket_path, 'status')
                if _mode(status) != 'STOPPED' and self.activity_before_read:
                    status = None       # read again once the drive is awake
            if status is None and self.activity_before_read:
                # Finish wake/verification before short status reads.
                # Failed or uncertain acknowledgement blocks transport.
                try:
                    self.sender(self.socket_path, 'drive-activity', None)
                except Exception as exc:
                    if action != 'eject':
                        raise
                    # A failed wake must not keep the disc inside: the
                    # player still verifies the drive before ejecting.
                    self.report('selected-target execute: drive wake unverified ('
                                + type(exc).__name__ + '); EJECT left to player checks')
            if status is None:
                status = self.reader(self.socket_path, 'status')
            target = (None if action == 'eject' else
                      self.reader(self.socket_path, 'get-target'))
            command = (plan_eject(status) if action == 'eject'
                       else plan_stop(status, target) if action == 'stop'
                       else plan_track_change(status, target, action)
                       if action in ('next', 'previous')
                       else plan_playpause(status, target))
            if command not in SAFE_COMMANDS:
                self.report('selected-target execute: blocked')
                if action == 'playpause':
                    self._notify('no_target' if not _has_selection(target) else 'blocked')
                return
            # Each command is a fresh, explicit physical-press intent.
            # The player independently checks live ownership/safety.
            # No UID is transmitted from the button service.
            sent = True     # a refusal from here on is reported by the player
            self.sender(self.socket_path, command, None)
            self.report('selected-target execute: ' + command + ' acknowledged')
        except Exception as exc:
            self.report('selected-target execute: unavailable or rejected (' +
                        type(exc).__name__ + '); not retrying')
            if action == 'playpause' and not sent:
                self._notify('blocked')   # e.g. the drive did not wake

    def _drain_pending_steps(self):
        """Send NEXT/PREVIOUS pressed meanwhile, one by one, while still STOPPED."""
        while True:
            with self._lock:
                steps = self._pending_steps
                self._pending_steps = 0
                if steps == 0:
                    self._busy = False
                    return
            try:
                status = self.reader(self.socket_path, 'status')
                target = self.reader(self.socket_path, 'get-target')
                if _mode(status) != 'STOPPED':
                    self.report('selected-target execute: %d queued track step(s) dropped '
                                '(player not stopped)' % abs(steps))
                    continue
                direction = 'next' if steps > 0 else 'previous'
                for _ in range(abs(steps)):
                    if plan_track_change(status, target, direction) != direction:
                        self.report('selected-target execute: queued track step blocked')
                        break
                    self.sender(self.socket_path, direction, None)
                self.report('selected-target execute: %d queued %s acknowledged'
                            % (abs(steps), direction))
            except Exception as exc:
                self.report('selected-target execute: queued track steps unavailable or rejected ('
                            + type(exc).__name__ + '); not retrying')

    def _notify(self, event):
        if self.notify is None:
            return
        try:
            self.notify(event)
        except Exception:
            pass               # feedback only; never blocks the button
