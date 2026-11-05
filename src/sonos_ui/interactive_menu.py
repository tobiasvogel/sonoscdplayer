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
from .i18n import Translator, available_languages
from .single_target import select_one, TargetSelectionError
from .target_rpc_client import submit_target
from .selection_plan import plan_from_applied


class InteractiveMenu:
    def __init__(self, *, discover=discover_targets, refresh_seconds=15.0, language='de', target_rpc=None, group_target_rpc=None):
        self.state = MultiSelection()
        self.language = Translator(language).language
        self.languages = available_languages()
        self.language_picker = False
        self.language_focus = self.languages.index(self.language)
        self.active = False
        self._discover = discover
        self._refresh_seconds = refresh_seconds
        self._next_refresh = 0.0
        self._pending = None
        self._lock = threading.Lock()
        self._worker = None
        self.renderer = MultiRenderer(language=self.language)
        self._target_rpc = target_rpc
        self._group_target_rpc = group_target_rpc
        self._target_pending = False
        self._target_result = None
        self._target_generation = 0
        self._target_worker = None
        self._pending_selection = None

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
            if self.state.topology.error:
                raise ValueError('topology unavailable')
            intent = plan_from_applied(self.state.topology.targets, self.state.draft)
        except (ValueError, TypeError):
            self.state = replace(self.state, notice='status.target_rejected')
            return
        if (intent.members and self._group_target_rpc is None) or (
                not intent.members and self._target_rpc is None):
            # In the safe read-only OLED mode, no player target RPC is enabled.
            # This is not evidence of an invalid speaker selection.
            self.state = replace(self.state, notice='status.target_unavailable')
            return
        self._target_pending = True
        self._target_generation += 1
        generation = self._target_generation
        self._pending_selection = self.state.draft
        uid = intent.coordinator
        members = intent.members
        def work():
            try:
                acknowledged = (self._group_target_rpc(uid, members) if members
                                else self._target_rpc(uid))
                if acknowledged is not True:
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
        if new is not None:
            # Topology refresh must not erase a completed target-RPC notice.
            self.state = self.state.update(new)
            changed = True
        with self._lock:
            result = self._target_result
            self._target_result = None
        if result is not None and result[0] == self._target_generation:
            self._target_pending = False
            if result[1] == 'status.target_accepted' and self._pending_selection is not None:
                # Only the player ACK commits the new local applied target.
                self.state = replace(self.state, applied=self._pending_selection,
                                     notice=result[1])
            else:
                self.state = replace(self.state, notice=result[1])
            self._pending_selection = None
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
                self.language_focus = min(len(self.languages)-1, self.language_focus+1)
            elif (name,event) == ('encoder','left'):
                self.language_focus = max(0,self.language_focus-1)
            elif (name,event) == ('encoder_sw','pressed'):
                self.language = self.languages[self.language_focus]
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
        self.state = self.state.action(action)
        if action == 'confirm' and self.state.notice == 'status.apply_requested':
            self._send_target()
        if self.state.notice == 'menu.back':
            self.active = False
            self.state = replace(self.state, notice='')
        elif self.state.notice == 'menu.language':
            self.language_picker = True
            self.language_focus = self.languages.index(self.language)
            self.state = replace(self.state, notice='')
        return True

    def frame(self, image_to_frame):
        if self.language_picker:
            from PIL import Image, ImageDraw
            t=self.renderer.theme
            im=Image.new('RGB',(128,128),t.background)
            self.renderer.shell(im,self.renderer.tr('menu.language'))
            # 128px OLED: keep focused language visible even with many files.
            top=max(0,min(max(0,len(self.languages)-4),self.language_focus-1))
            for row_index, index in enumerate(range(top,min(top+4,len(self.languages)))):
                lang=self.languages[index]
                y=22+row_index*22
                if index==self.language_focus:
                    ImageDraw.Draw(im).rounded_rectangle((4,y,123,y+19),radius=3,fill=t.accent)
                col=t.selection_text if index==self.language_focus else t.primary
                label=self.renderer.translator.language_name(lang)
                self.renderer.text(im,(10,y+3),label,fill=col,size=11,width=110,height=15)
            self.renderer.bottom(im,status=self.renderer.tr('status.local_only'),
                                 footer=self.renderer.tr('footer.confirm'))
            return image_to_frame(im)
        return image_to_frame(self.renderer.render_multi(self.state,gray=True))
