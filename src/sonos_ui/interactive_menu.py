"""Opt-in interactive menu orchestration for the existing OLED service.

No GPIO ownership and no Sonos writes. Discovery happens off the display loop,
so ambient contrast and player polling remain responsive. The only output is
a local selection view; a confirmed checkbox selection never controls playback.
"""
from __future__ import annotations
import threading
from dataclasses import replace
import time
from .multi_target import MultiSelection, discover_targets
from .multi_render import MultiRenderer
from .i18n import Translator
from .single_target import select_one, TargetSelectionError
from .target_rpc_client import submit_target


class InteractiveMenu:
    def __init__(self, *, discover=discover_targets, refresh_seconds=15.0, language='de', target_rpc=None):
        self.state = MultiSelection()
        self.language = Translator(language).language
        self.language_picker = False
        self.language_focus = ('de','en').index(self.language)
        self.active = False
        self._discover = discover
        self._refresh_seconds = refresh_seconds
        self._next_refresh = 0.0
        self._pending = None
        self._lock = threading.Lock()
        self._worker = None
        self.renderer = MultiRenderer(language=self.language)
        self._target_rpc = target_rpc
        self._target_pending = False
        self._target_result = None
        self._target_generation = 0
        self._target_worker = None

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

    def _send_target(self):
        """Off-display worker, one request only, no UID in logs or UI state."""
        if self._target_pending:
            return
        try:
            intent = select_one(self.state)
        except (TargetSelectionError, TypeError):
            self.state = replace(self.state, notice='status.target_rejected')
            return
        self._target_pending = True
        self._target_generation += 1
        generation = self._target_generation
        uid = intent.uid
        def work():
            try:
                if self._target_rpc(uid) is not True:
                    raise RuntimeError('target was not acknowledged')
                result = 'status.target_accepted'
            except Exception:
                result = 'status.target_rejected'
            with self._lock:
                self._target_result = (generation, result)
        self.state = replace(self.state, notice='status.target_pending')
        self._target_worker = threading.Thread(target=work, name='ui-target-selection',daemon=True)
        self._target_worker.start()

    def poll(self, now=None):
        """Accept one completed discovery and periodically refresh while open."""
        now = time.monotonic() if now is None else now
        changed = False
        with self._lock:
            new = self._pending
            self._pending = None
        with self._lock:
            result = self._target_result
            self._target_result = None
        if result is not None and result[0] == self._target_generation:
            self._target_pending = False
            self.state = replace(self.state,notice=result[1])
            changed = True
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
        # Connect is deliberately not a menu input. It remains available for
        # future explicit Sonos connectivity actions in the player layer.
        if name == 'connect':
            return False
        if name == 'encoder_sw' and event == 'pressed' and not self.active:
            self.active = True
            self.language_picker = False
            self._start_discovery(now)
            return True
        if not self.active:
            return False
        if self._target_pending:
            return False
        if self.language_picker:
            if (name, event) == ('encoder','right'):
                self.language_focus = min(1, self.language_focus+1)
            elif (name,event) == ('encoder','left'):
                self.language_focus = max(0,self.language_focus-1)
            elif (name,event) == ('encoder_sw','pressed'):
                self.language = ('de','en')[self.language_focus]
                self.renderer = MultiRenderer(language=self.language)
                self.language_picker = False
                self.state = replace(self.state, notice='')
            else:
                return False
            return True
        action = {('encoder','right'): 'next',
                  ('encoder','left'): 'previous',
                  ('encoder_sw','pressed'): 'confirm'}.get((name,event))
        if action is None:
            return False
        was_reviewing = self.state.reviewing
        self.state = self.state.action(action)
        if (was_reviewing and action == 'confirm'
                and self.state.notice == 'status.local_applied'
                and self._target_rpc is not None):
            self._send_target()
        if self.state.notice == 'menu.back':
            self.active = False
            self.state = replace(self.state, notice='')
        elif self.state.notice == 'menu.language':
            self.language_picker = True
            self.language_focus = ('de','en').index(self.language)
            self.state = replace(self.state, notice='')
        return True

    def frame(self, image_to_frame):
        if self.language_picker:
            from PIL import Image, ImageDraw
            t=self.renderer.theme
            im=Image.new('RGB',(128,128),t.background)
            self.renderer.shell(im,self.renderer.tr('menu.language'))
            for index,key in enumerate(('menu.language_de','menu.language_en')):
                y=34+index*24
                if index==self.language_focus:
                    ImageDraw.Draw(im).rounded_rectangle((4,y,123,y+19),radius=3,fill=t.accent)
                col=t.selection_text if index==self.language_focus else t.primary
                self.renderer.text(im,(10,y+3),self.renderer.tr(key),fill=col,size=11,width=110,height=15)
            self.renderer.bottom(im,status=self.renderer.tr('status.local_only'),
                                 footer=self.renderer.tr('footer.confirm'))
            return image_to_frame(im)
        return image_to_frame(self.renderer.render_multi(self.state,gray=True))
