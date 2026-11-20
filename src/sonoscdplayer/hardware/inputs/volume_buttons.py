"""Physical volume buttons (auto-repeat and one-shot RPCs).

The GPIO loop calls VolumeRepeat.event()/tick() and hands the resulting steps to
VolumeExecutor.submit(). Only one request may be in flight. Repeats that arrive
while it is busy are dropped, never accumulated or replayed after a slow drive
wake or Sonos response. Failed or uncertain requests are never retried. The
player decides whether a change is allowed.
"""
from __future__ import annotations

import threading
import time

VOLUME_BUTTONS = {'volume_up': 1, 'volume_dn': -1}
NO_TARGET_NOTICE_COOLDOWN = 2.0


class NoTargetSelected(ValueError):
    """The player rejected volume because there is no confirmed target."""


class VolumeRepeat:
    """Press: one step now. Held: repeat after delay_ms, then every interval_ms."""

    def __init__(self, delay_ms=750, interval_ms=250):
        self.delay_ms = delay_ms
        self.interval_ms = interval_ms
        self._held = None
        self._next_ms = None

    def event(self, name, event, now_ms):
        """A button changed; returns the step (+1/-1) on a volume press, else 0."""
        direction = VOLUME_BUTTONS.get(name)
        if direction is None:
            return 0
        if event == 'pressed':
            self._held = name
            self._next_ms = now_ms + self.delay_ms
            return direction
        if event == 'released' and self._held == name:
            self._held = None
            self._next_ms = None
        return 0

    def tick(self, now_ms):
        """Regular call; returns a step when an auto-repeat is due."""
        if self._held is None or now_ms < self._next_ms:
            return 0
        self._next_ms = max(self._next_ms + self.interval_ms, now_ms)
        return VOLUME_BUTTONS[self._held]


class VolumeExecutor:
    """Sends volume steps to the player, one at a time."""
    def __init__(self, socket_path, *, sender, publish=None, report=print):
        self.socket_path = socket_path
        self.sender = sender          # (socket_path, command) -> reply dict
        self.publish = publish        # (name, event) -> bool, best effort UI
        self.report = report
        self._lock = threading.Lock()
        self._busy = False
        self._last_no_target_notice = float('-inf')

    def submit(self, step):
        """Send ``step`` in the background; dropped while one is in flight."""
        if step == 0:
            return False
        if step not in (-1, 1):
            raise ValueError('volume button step must be -1 or +1')
        with self._lock:
            if self._busy:
                return False  # Never queue a repeat behind a blocked RPC.
            self._busy = True
        try:
            threading.Thread(target=self._run, args=(step,),
                             name='volume-button', daemon=True).start()
        except Exception:
            with self._lock:
                self._busy = False
            raise
        return True

    def _run(self, step):
        try:
            command = f'volume {step:+d}'
            try:
                reply = self.sender(self.socket_path, command)
                level = reply.get('volume')
                if type(level) is int and 0 <= level <= 100:
                    if self.publish is not None:
                        self.publish('volume', str(level))
                    self.report(f'volume: {command} -> {level}')
                else:
                    self.report(f'volume: {command} acknowledged')
            except NoTargetSelected:
                # Do not flood the UI while VOL is held.
                now = time.monotonic()
                with self._lock:
                    notify = now - self._last_no_target_notice >= NO_TARGET_NOTICE_COOLDOWN
                    if notify:
                        self._last_no_target_notice = now
                if notify and self.publish is not None:
                    self.publish('volume', 'no_target')
                self.report('volume: no selected target; not retrying')
            except Exception as exc:
                self.report('volume: unavailable or rejected ('
                            + type(exc).__name__ + '); not retrying')
        finally:
            with self._lock:
                self._busy = False
