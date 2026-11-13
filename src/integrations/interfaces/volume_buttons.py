"""INP-02: physical volume buttons (auto-repeat and coalesced, one-shot RPCs).

The GPIO loop calls VolumeRepeat.event()/tick() and hands the resulting steps to
VolumeExecutor.submit(). The executor runs at most one player request at a time;
steps arriving meanwhile are summed and sent together afterwards, so holding a
button never builds up a backlog. A failed or uncertain request is never
retried and drops pending steps. The player decides whether a change is allowed.
"""
from __future__ import annotations

import threading

VOLUME_BUTTONS = {'volume_up': 1, 'volume_dn': -1}
MAX_STEP = 10  # must match volume_control.MAX_STEP in the player


class VolumeRepeat:
    """Press: one step now. Held: repeat after delay_ms, then every interval_ms."""

    def __init__(self, delay_ms=750, interval_ms=250):
        self.delay_ms = delay_ms
        self.interval_ms = interval_ms
        self._held = None
        self._next_ms = None

    def event(self, name, event, now_ms):
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
        if self._held is None or now_ms < self._next_ms:
            return 0
        self._next_ms = max(self._next_ms + self.interval_ms, now_ms)
        return VOLUME_BUTTONS[self._held]


class VolumeExecutor:
    def __init__(self, socket_path, *, sender, publish=None, report=print):
        self.socket_path = socket_path
        self.sender = sender          # (socket_path, command) -> reply dict
        self.publish = publish        # (name, event) -> bool, best effort UI
        self.report = report
        self._lock = threading.Lock()
        self._pending = 0
        self._busy = False

    def submit(self, step):
        if step == 0:
            return False
        with self._lock:
            self._pending = max(-MAX_STEP, min(MAX_STEP, self._pending + step))
            if self._busy:
                return True
            self._busy = True
        threading.Thread(target=self._run, name='volume-button', daemon=True).start()
        return True

    def _take(self):
        with self._lock:
            step, self._pending = self._pending, 0
            if step == 0:
                self._busy = False
            return step

    def _run(self):
        step = self._take()
        while step:
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
            except Exception as exc:
                self.report('volume: unavailable or rejected ('
                            + type(exc).__name__ + '); not retrying')
                with self._lock:
                    self._pending = 0
            step = self._take()
