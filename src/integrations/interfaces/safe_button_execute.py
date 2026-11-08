"""Opt-in M0.18 safe PLAY/PAUSE command path.

Read status and selection once per physical press, apply the fail-closed policy,
then attempt exactly one player transport command. No retry, queue, UID, or
implicit fallback to the legacy 'playpause' RPC.
"""
import threading

try:
    from .safe_button_policy import plan_playpause, plan_stop, plan_track_change, plan_eject
    from .safe_button_preview import read_snapshot
except ImportError:
    from safe_button_policy import plan_playpause, plan_stop, plan_track_change, plan_eject
    from safe_button_preview import read_snapshot


SAFE_COMMANDS = frozenset(('play-selected', 'pause', 'resume', 'stop', 'next', 'previous', 'eject'))


class SelectedTargetExecutor:
    """Nonblocking one-shot transport worker; at most one in flight."""

    def __init__(self, socket_path, *, reader=read_snapshot, sender=None,
                 report=print, activity_before_read=False):
        if sender is None:
            raise ValueError('a one-shot sender must be supplied')
        self.socket_path = socket_path
        self.reader = reader
        self.sender = sender
        self.report = report
        self.activity_before_read = activity_before_read
        self._lock = threading.Lock()
        self._busy = False

    def submit(self, action='playpause'):
        if action not in ('playpause', 'stop', 'next', 'previous', 'eject'):
            return False
        with self._lock:
            if self._busy:
                return False
            self._busy = True

        def perform():
            try:
                if self.activity_before_read:
                    # Finish wake/verification before short status reads.
                    # Failed or uncertain acknowledgement blocks transport.
                    self.sender(self.socket_path, 'drive-activity', None)
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
                    return
                # Each command is a fresh, explicit physical-press intent.
                # The player independently checks live ownership/safety.
                # No UID is transmitted from the button service.
                self.sender(self.socket_path, command, None)
                self.report('selected-target execute: ' + command + ' acknowledged')
            except Exception as exc:
                self.report('selected-target execute: unavailable or rejected (' +
                            type(exc).__name__ + '); not retrying')
            finally:
                with self._lock:
                    self._busy = False

        try:
            threading.Thread(target=perform, name='selected-target-execute',
                             daemon=True).start()
        except Exception:
            with self._lock:
                self._busy = False
            raise
        return True
