"""Opt-in interactive menu orchestration for the existing OLED service.

No GPIO ownership and no Sonos writes. Discovery happens off the display loop,
so ambient contrast and player polling remain responsive. The only output is
a local selection view; a confirmed checkbox selection never controls playback.
"""
from __future__ import annotations
import threading
import time
from .multi_target import MultiSelection, discover_targets
from .multi_render import MultiRenderer


class InteractiveMenu:
    def __init__(self, *, discover=discover_targets, refresh_seconds=15.0):
        self.state = MultiSelection()
        self.active = False
        self._discover = discover
        self._refresh_seconds = refresh_seconds
        self._next_refresh = 0.0
        self._pending = None
        self._lock = threading.Lock()
        self._worker = None
        self.renderer = MultiRenderer()

    def _start_discovery(self, now):
        if self._worker is not None and self._worker.is_alive():
            return
        self._next_refresh = now + self._refresh_seconds
        def work():
            try:
                result = self._discover(timeout=3.0)
            except Exception as exc:
                from .multi_target import Topology
                result = Topology((), type(exc).__name__)
            with self._lock:
                self._pending = result
        self._worker = threading.Thread(target=work, name='ui-discovery', daemon=True)
        self._worker.start()

    def poll(self, now=None):
        """Accept one completed discovery and periodically refresh while open."""
        now = time.monotonic() if now is None else now
        changed = False
        with self._lock:
            new = self._pending
            self._pending = None
        if new is not None:
            # Revalidation may invalidate the review; never blindly apply old IDs.
            self.state = self.state.update(new)
            changed = True
        if self.active and now >= self._next_refresh:
            self._start_discovery(now)
        return changed

    def event(self, name, event, now=None):
        now = time.monotonic() if now is None else now
        if event != 'pressed' and name != 'encoder':
            return False
        if name == 'connect' and event == 'pressed':
            if self.active:
                self.active = False
                self.state = self.state.action('back')
            else:
                self.active = True
                self._start_discovery(now)
            return True
        if not self.active:
            return False
        action = {('encoder','right'): 'next',
                  ('encoder','left'): 'previous',
                  ('encoder_sw','pressed'): 'confirm'}.get((name,event))
        if action is None:
            return False
        self.state = self.state.action(action)
        return True

    def frame(self, image_to_frame):
        return image_to_frame(self.renderer.render_multi(self.state,gray=True))
