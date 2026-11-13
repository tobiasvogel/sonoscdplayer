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
from .connect_notice import ConnectNoticeView
from .notice_render import NoticeRenderer
from .navigation_pages import NavigationRenderer, MAIN_ITEMS, NETWORK_ITEMS, SETTINGS_ITEMS
from .network_status import NetworkStatus, read_network_status


POWER_ITEMS = ('back', 'poweroff', 'reboot')
# After a player-acknowledged "Auswahl übernehmen" the confirmation stays
# visible this long, then the menu closes to the overview.
ACCEPTED_CLOSE_SECONDS = 1.5
# INP-02: how long an acknowledged volume level stays visible.
VOLUME_OVERLAY_SECONDS = 2.0


class InteractiveMenu:
    def __init__(self, *, discover=discover_targets, refresh_seconds=15.0, language='de', target_rpc=None, group_target_rpc=None, power_action=None, network_reader=read_network_status):
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
        # Power menu: only available when a power_action(action) -> bool callable
        # is injected (fail closed). It runs on a worker thread, never on the display loop.
        self._power_action = power_action
        self.power_menu = False
        self.power_focus = 0
        self.power_notice = ''
        self._power_busy = False
        self._power_result = None
        # Menu position to restore when the power menu is closed with "back";
        # None means the overview (no menu was open).
        self._power_return = None
        self._close_menu_at = None
        self._volume_level = None
        self._volume_until = 0.0
        self.connect_notices = ConnectNoticeView()
        self._connect_renderer = NoticeRenderer()
        self._notice_previous_active = None
        self.section = 'targets'  # Preserve direct Sonos selection from CONNECT.
        self.menu_focus = 0
        self._targets_return = 'closed'
        self._navigation_renderer = NavigationRenderer()
        self._network_reader = network_reader
        self.network_snapshot = NetworkStatus()
        self._network_pending = None
        self._network_worker = None
        self._next_network_refresh = 0.0

    def _open_network_status(self, now):
        self.section = 'network_status'
        self.network_snapshot = NetworkStatus()
        self._next_network_refresh = 0.0
        self._start_network_read(now)

    def _start_network_read(self, now):
        if self._network_worker is not None and self._network_worker.is_alive():
            return
        self._next_network_refresh = now + 15.0
        def work():
            try:
                snapshot = self._network_reader()
                if not isinstance(snapshot, NetworkStatus):
                    snapshot = NetworkStatus()
            except Exception:
                snapshot = NetworkStatus()
            with self._lock:
                self._network_pending = snapshot
        self._network_worker = threading.Thread(
            target=work, name='ui-network-status', daemon=True)
        self._network_worker.start()

    def _navigation_event(self, name, event, now):
        if name == 'encoder' and event in ('left', 'right'):
            choices = {'main': MAIN_ITEMS, 'network': NETWORK_ITEMS,
                       'settings': SETTINGS_ITEMS}.get(self.section)
            if choices is None:
                return False
            step = 1 if event == 'right' else -1
            self.menu_focus = max(0, min(len(choices) - 1, self.menu_focus + step))
            return True
        if (name, event) != ('encoder_sw', 'pressed'):
            return False
        if self.section == 'network_status':
            self.section = 'network'
            self.menu_focus = 0
            return True
        if self.section not in ('main', 'network', 'settings'):
            return False
        if self.section == 'main':
            if self.menu_focus == 0:
                self._targets_return = 'main'
                self.section = 'targets'
                self.state = replace(self.state, focus=0)
                self._next_refresh = 0.0
                self._start_discovery(now)
            elif self.menu_focus == 1:
                self.section = 'network'
                self.menu_focus = 0
            elif self.menu_focus == 2:
                self.section = 'settings'
                self.menu_focus = 0
            else:
                self.active = False
        elif self.section == 'network':
            if self.menu_focus == 0:
                self._open_network_status(now)
            elif self.menu_focus == 1:
                self._connect_event('settings', now)
            else:
                self.section = 'main'
                self.menu_focus = 1
        elif self.section == 'settings':
            if self.menu_focus == 0:
                self.language_picker = True
                self.language_focus = self.languages.index(self.language)
            else:
                self.section = 'main'
                self.menu_focus = 2
        return True

    def _open_targets(self, now):
        """Open existing target selection, never start playback."""
        self.connect_notices.clear(now=now)
        self._notice_previous_active = None
        self.active = True
        self.section = 'targets'
        self._targets_return = 'closed'
        self.state = replace(self.state, focus=0)
        self.language_picker = False
        self._next_refresh = 0.0
        self._start_discovery(now)
        return True

    def _connect_event(self, action, now):
        if self._power_busy or self.power_menu:
            return False
        if action not in ('status', 'settings', 'settings_hint', 'setup_requested',
                          'warning_1', 'warning_2', 'warning_3',
                          'warning_4', 'warning_5'):
            return False
        if self._notice_previous_active is None:
            self._notice_previous_active = self.active
        self.connect_notices.show(action, now=now)
        self.active = True
        return True

    def _restore_after_notice(self):
        if self._notice_previous_active is not None:
            self.active = self._notice_previous_active
            self._notice_previous_active = None

    def _power_event(self, name, event):
        """Handle input while the power menu is open. Returns True if the frame changed."""
        if self._power_busy:
            return False  # shutting down or restarting: ignore everything
        if (name, event) == ('power', 'short'):
            return self._close_power()
        if (name, event) == ('encoder', 'right'):
            self.power_focus = min(len(POWER_ITEMS) - 1, self.power_focus + 1)
        elif (name, event) == ('encoder', 'left'):
            self.power_focus = max(0, self.power_focus - 1)
        elif (name, event) == ('encoder_sw', 'pressed'):
            choice = POWER_ITEMS[self.power_focus]
            if choice == 'back':
                return self._close_power()
            self._power_busy = True
            self.power_notice = 'status.power_' + choice
            def work():
                try:
                    ok = self._power_action(choice) is True
                except Exception:
                    ok = False
                with self._lock:
                    self._power_result = ok
            threading.Thread(target=work, name='ui-power-action', daemon=True).start()
        else:
            return False
        self.power_notice = self.power_notice if self._power_busy else ''
        return True

    def _open_power(self):
        """Overlay the power menu and remember where the user was."""
        if self.active:
            # The language picker is not restored: it reopens on the saved
            # language anyway, so return to its "language" entry instead.
            section, focus = (('settings', 0) if self.language_picker
                              else (self.section, self.menu_focus))
            self._power_return = (section, focus)
        else:
            self._power_return = None
        self.power_menu = True
        self.language_picker = False
        self.power_focus = 0  # "Zurück" is always the default
        self.power_notice = ''
        self.active = True
        return True

    def _close_power(self):
        """Return to the menu that was open before (unapplied checkboxes kept)."""
        self.power_menu = False
        self.power_notice = ''
        restore = self._power_return
        self._power_return = None
        if restore is None:
            self.active = False
            return True
        self.section, self.menu_focus = restore
        self.active = True
        if self.section == 'network_status':
            self._next_network_refresh = 0.0
        elif self.section == 'targets':
            self._next_refresh = 0.0
        return True

    def _start_discovery(self, now):
        if self._worker is not None and self._worker.is_alive():
            return
        self._next_refresh = now + self._refresh_seconds
        language=self.language
        def work():
            try:
                if self._discover is discover_targets:
                    result = discover_targets(timeout=3.0, language=language)
                else:
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
            power_result = self._power_result
            self._power_result = None
            network_result = self._network_pending
            self._network_pending = None
        if power_result is False:
            # The system did not accept the request: the menu stays open and usable.
            self._power_busy = False
            self.power_notice = 'status.power_failed'
            changed = True
        if network_result is not None:
            self.network_snapshot = network_result
            changed = True
        if self.active and self.section == 'network_status' and not self.power_menu and self._notice_previous_active is None and now >= self._next_network_refresh:
            self._start_network_read(now)
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
                # Success closes the menu after a short confirmation; a power
                # menu opened meanwhile then returns to the overview.
                if self.power_menu:
                    if self._power_return is not None and self._power_return[0] == 'targets':
                        self._power_return = None
                elif self.active and self.section == 'targets':
                    self._close_menu_at = now + ACCEPTED_CLOSE_SECONDS
            else:
                self.state = replace(self.state, notice=result[1])
            self._pending_selection = None
            changed = True
        if self._close_menu_at is not None and now >= self._close_menu_at:
            self._close_menu_at = None
            if (self.active and self.section == 'targets' and not self.power_menu
                    and self._notice_previous_active is None):
                self.active = False
                self.state = replace(self.state, notice='', focus=0)
                changed = True
        if self._notice_previous_active is not None and self.connect_notices.current(now=now) is None:
            self._restore_after_notice()
            changed = True
        if (self.active and self.section == 'targets' and not self.power_menu and
                self._notice_previous_active is None and now >= self._next_refresh):
            self._start_discovery(now)
        return changed

    def volume_overlay_active(self, now=None):
        now = time.monotonic() if now is None else now
        return self._volume_level is not None and now < self._volume_until

    def event(self, name, event, now=None):
        now = time.monotonic() if now is None else now
        if name == 'volume':
            # Display only: the level was already set and acknowledged by the player.
            if not (isinstance(event, str) and event.isdigit() and 0 <= int(event) <= 100):
                return False
            self._volume_level = int(event)
            self._volume_until = now + VOLUME_OVERLAY_SECONDS
            return True
        if (name, event) == ('speaker_select', 'pressed'):
            if self._power_busy or self.power_menu:
                return False
            return self._open_targets(now)
        if name == 'connect':
            return self._connect_event(event, now)
        if (name, event) == ('power', 'short') and self._power_action is not None:
            self.connect_notices.clear(now=now)
            self._restore_after_notice()
        if (self._notice_previous_active is not None and
                self.connect_notices.current(now=now) is not None):
            if (name, event) == ('encoder_sw', 'pressed'):
                if self.connect_notices.confirm(now=now):
                    self._restore_after_notice()
                    return True
            # A displayed notice consumes encoder input rather than
            # changing an obscured speaker selection.
            return False
        if self._power_action is not None and (name, event) == ('power', 'short'):
            if self.power_menu:
                return self._power_event(name, event)
            # Short power press opens the power menu from anywhere as an overlay;
            # a pending target request keeps running unseen.
            return self._open_power()
        if self.power_menu:
            return self._power_event(name, event)
        if name == 'power':
            return False
        if event != 'pressed' and name != 'encoder':
            return False
        # CONNECT semantic actions are handled before ordinary menu inputs.
        if name == 'encoder_sw' and event == 'pressed' and not self.active:
            self.active = True
            self.section = 'main'
            self.menu_focus = 0
            self.language_picker = False
            return True
        if not self.active:
            return False
        if self._target_pending and self.section == 'targets':
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
                # Rebuild localized fallback names after a user language switch.
                self._next_refresh = 0.0
            else:
                return False
            return True
        if self.section != 'targets':
            return self._navigation_event(name, event, now)
        action = {('encoder','right'): 'next',
                  ('encoder','left'): 'previous',
                  ('encoder_sw','pressed'): 'confirm'}.get((name,event))
        if action is None:
            return False
        self._close_menu_at = None  # user is still working in the list
        self.state = self.state.action(action)
        if action == 'confirm' and self.state.notice == 'status.apply_requested':
            self._send_target()
        if self.state.notice == 'menu.back':
            if self._targets_return == 'main':
                self.section = 'main'
                self.menu_focus = 0
            else:
                self.active = False
            self.state = replace(self.state, notice='')
        return True

    def frame(self, image_to_frame):
        active_notice = (self.connect_notices.current(now=time.monotonic())
                         if self._notice_previous_active is not None else None)
        if active_notice is not None:
            return image_to_frame(self._connect_renderer.render(
                active_notice, language=self.language, grayscale=True))
        if self.power_menu:
            return image_to_frame(self.renderer.render_power(
                self.power_focus, self.power_notice, gray=True))
        if self.volume_overlay_active():
            return image_to_frame(self.renderer.render_volume(self._volume_level, gray=True))
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
        if self.section in ('main', 'network', 'settings'):
            items = {'main': MAIN_ITEMS, 'network': NETWORK_ITEMS,
                     'settings': SETTINGS_ITEMS}[self.section]
            heading = {'main': 'menu.main', 'network': 'menu.network',
                       'settings': 'menu.settings'}[self.section]
            return image_to_frame(self._navigation_renderer.page(
                heading, items, self.menu_focus, language=self.language))
        if self.section == 'network_status':
            return image_to_frame(self._navigation_renderer.network_status(
                self.network_snapshot, language=self.language))
        return image_to_frame(self.renderer.render_multi(self.state,gray=True))
