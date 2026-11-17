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
from .notice import Notice
from .notice_render import NoticeRenderer
from .notice_policy import NoticeTiming
from .text_input import DEFAULT_EXTRA_CHARACTERS
from .wifi_entry import WifiEntryController
from .navigation_pages import NavigationRenderer
from .menu_catalog import NAVIGATION_PAGES
from .network_status import NetworkStatus, read_network_status


POWER_ITEMS = ('back', 'poweroff', 'reboot')
# After a player-acknowledged "Auswahl übernehmen" the menu closes and a
# success notice stays visible this long.
ACCEPTED_NOTICE_SECONDS = 1.0
SCAN_RESULT_SECONDS = 2.0
# Speakers are discovered once when the OLED service starts and cached; only an
# empty or failed result is retried in the background. "Find speakers" in the
# settings menu forces a new discovery.
EMPTY_SCAN_RETRY_SECONDS = 30.0
CURRENT_TARGET_RETRY_SECONDS = 5.0
# A power-off requested by the button service is not acknowledged to the UI;
# if the system is still running after this long, show the normal UI again.
LONG_PRESS_SCREEN_SECONDS = 90.0
# INP-02: how long an acknowledged volume level stays visible.
VOLUME_OVERLAY_SECONDS = 2.0


class InteractiveMenu:
    def __init__(self, *, discover=discover_targets, refresh_seconds=15.0, language='de', target_rpc=None, group_target_rpc=None, power_action=None, network_reader=read_network_status, current_target=None, on_language_change=None, network_setup=None, text_extra_characters=DEFAULT_EXTRA_CHARACTERS):
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
        # 'transition' (power-off) or 'reboot' once a shutdown was requested:
        # the full localized system screen replaces everything until the end.
        self.shutdown_screen = None
        self._shutdown_frame = None
        self._shutdown_deadline = None
        # Menu position to restore when the power menu is closed with "back";
        # None means the overview (no menu was open).
        self._power_return = None
        self._discovery_started = False
        self._scan_requested = False
        # Player's remembered selection (e.g. after a restart) -> checkmarks.
        self._current_target = current_target
        # Called with the new language code after a confirmed menu choice.
        self._on_language_change = on_language_change
        # WLAN setup through the hotspot (NET-01); None = not available here.
        self.network_setup = network_setup
        # On-device WLAN choice from the hotspot screen (D-224).
        self.wifi_entry = (WifiEntryController(network_setup,
                                               extra_characters=text_extra_characters)
                           if network_setup is not None else None)
        self._netsetup_last = None
        self._netsetup_was_active = None
        self.player_mode = None   # updated by the OLED loop from the player status
        self._current_loaded = current_target is None
        self._current_worker = None
        self._current_result = None
        self._next_current_try = 0.0
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
        """Resolve a focused menu key before executing its existing action.

        The catalog owns presentation order; the controller alone owns effects.
        Unknown keys are rejected, never interpreted as a fallback/back action.
        """
        if name == 'encoder' and event in ('left', 'right'):
            page = NAVIGATION_PAGES.get(self.section)
            choices = page[1] if page is not None else None
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
        page = NAVIGATION_PAGES.get(self.section)
        if page is None:
            return False
        choices = page[1]
        if not 0 <= self.menu_focus < len(choices):
            return False
        action = choices[self.menu_focus]
        section = self.section

        if section == 'main':
            if action == 'menu.targets':
                self._targets_return = 'main'
                self.section = 'targets'
                self.state = replace(self.state, focus=0)
                self._discover_if_empty(now)
            elif action == 'menu.network':
                self.section = 'network'
                self.menu_focus = 0
            elif action == 'menu.settings':
                self.section = 'settings'
                self.menu_focus = 0
            elif action == 'menu.back':
                self.active = False
            else:
                return False
        elif section == 'network':
            if action == 'network.status':
                self._open_network_status(now)
            elif action == 'network.setup':
                if self.network_setup is not None:
                    self._start_network_setup(now)
                else:
                    self._connect_event('settings', now)
            elif action == 'menu.back':
                self.section = 'main'
                self.menu_focus = NAVIGATION_PAGES['main'][1].index('menu.network')
            else:
                return False
        elif section == 'settings':
            if action == 'menu.language':
                self.language_picker = True
                self.language_focus = self.languages.index(self.language)
            elif action == 'menu.rescan':
                self._request_scan(now)
            elif action == 'menu.back':
                self.section = 'main'
                self.menu_focus = NAVIGATION_PAGES['main'][1].index('menu.settings')
            else:
                return False
        else:
            return False
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
        self._discover_if_empty(now)
        return True

    def searching(self):
        """A speaker search is running in the background."""
        return self._worker is not None and self._worker.is_alive()

    def _has_targets(self):
        return bool(self.state.topology.targets) and not self.state.topology.error

    def _discover_if_empty(self, now):
        """Use the cached speaker list; only search when there is none yet."""
        if not self._has_targets():
            self._start_discovery(now)

    def _request_scan(self, now):
        """Settings > Find speakers: forced discovery with visible progress."""
        self._scan_requested = True
        self._show_notice(Notice('status.scan_running', priority=40,
                                 dismissible=False), now)
        self._start_discovery(now)

    def _show_notice(self, notice, now):
        if self._notice_previous_active is None:
            self._notice_previous_active = self.active
        self.connect_notices.post(notice, now=now)
        self.active = True

    def netsetup_snapshot(self):
        if self.network_setup is None:
            return None
        snap = self.network_setup.snapshot()
        return snap if snap.state != 'idle' else None

    def _start_network_setup(self, now):
        """CONNECT >= 10 s or menu: start, or cancel a running setup."""
        if self.network_setup.active:
            self.network_setup.cancel()
            # Drop the cancel countdown together with the screen it remembered
            # (the setup screen), so the end notice returns to what was shown
            # before the setup started.
            self.connect_notices.clear(now=now)
            self._notice_previous_active = None
            if self._netsetup_was_active is not None:
                self.active = self._netsetup_was_active
                self._netsetup_was_active = None
            self._show_notice(Notice('netsetup.cancelled', priority=40, duration_seconds=2.0), now)
            return True
        if self.player_mode in ('STARTING', 'PLAYING', 'PAUSED'):
            # The WLAN chip cannot be hotspot and client at once: playback would stop.
            self._show_notice(Notice('netsetup.blocked', severity='warning', priority=40,
                                     duration_seconds=3.0), now)
            return True
        # Drop the CONNECT countdown first, so the screen before it comes back later.
        self.connect_notices.clear(now=now)
        self._restore_after_notice()
        if self._netsetup_was_active is None:
            self._netsetup_was_active = self.active
        self.network_setup.start()
        self.active = True
        return True

    def _connect_event(self, action, now):
        if self._power_busy or self.power_menu:
            return False
        if self.network_setup is not None and action == 'setup_requested':
            return self._start_network_setup(now)
        if self.netsetup_snapshot() is not None:
            return self._connect_during_setup(action, now)
        if self.network_setup is not None and action == 'settings':
            # CONNECT 3-10 s: the network menu with "Set up Wi-Fi".
            self.connect_notices.clear(now=now)
            self._restore_after_notice()
            self.active = True
            self.section = 'network'
            self.menu_focus = 1
            return True
        if action not in ('status', 'settings', 'settings_hint', 'setup_requested',
                          'warning_1', 'warning_2', 'warning_3',
                          'warning_4', 'warning_5'):
            return False
        if self._notice_previous_active is None:
            self._notice_previous_active = self.active
        self.connect_notices.show(action, now=now)
        self.active = True
        return True

    def _connect_during_setup(self, action, now):
        """CONNECT held while the setup runs: only the way out (10 s) counts."""
        if action.startswith('warning_') and action[8:] in ('1', '2', '3', '4', '5'):
            if self._notice_previous_active is None:
                self._notice_previous_active = self.active
            self.connect_notices.post(Notice('netsetup.cancel_warning', severity='warning',
                                             params={'seconds': int(action[8:])}, priority=80,
                                             duration_seconds=0, dismissible=False), now=now)
            return True
        if action == 'status' and self.connect_notices.current(now=now) is None:
            # Short press (with a separate speaker button): "Done" in the text entry.
            return self.wifi_entry.short_connect(self.netsetup_snapshot())
        if action in ('status', 'settings'):
            # Released before 10 s: the setup simply continues.
            changed = self.connect_notices.clear(now=now)
            self._restore_after_notice()
            return bool(changed)
        return False

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
            self._show_shutdown_screen('reboot' if choice == 'reboot' else 'transition')
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
        return True

    def _start_discovery(self, now):
        if self._worker is not None and self._worker.is_alive():
            return
        self._discovery_started = True
        self._next_refresh = now + EMPTY_SCAN_RETRY_SECONDS
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
        if self._shutdown_deadline is not None and now >= self._shutdown_deadline:
            self.shutdown_screen = None
            self._shutdown_deadline = None
            self.active = False
            changed = True
        if power_result is False:
            # The system did not accept the request: the menu stays open and usable.
            self._power_busy = False
            self.power_notice = 'status.power_failed'
            self.shutdown_screen = None
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
            if self._scan_requested:
                self._scan_requested = False
                if not self.power_menu:
                    count = 0 if self.state.topology.error else len(self.state.topology.targets)
                    if count:
                        notice = Notice('status.scan_done_one' if count == 1 else 'status.scan_done',
                                        severity='success', params={'count': count},
                                        priority=40, duration_seconds=SCAN_RESULT_SECONDS)
                    else:
                        notice = Notice('status.scan_none', severity='warning', priority=40,
                                        duration_seconds=SCAN_RESULT_SECONDS)
                    self.connect_notices.clear(now=now)
                    self._show_notice(notice, now)
        changed = self._poll_current_target(now) or changed
        changed = self._poll_network_setup() or changed
        with self._lock:
            result = self._target_result
            self._target_result = None
        if result is not None and result[0] == self._target_generation:
            self._target_pending = False
            if result[1] == 'status.target_accepted' and self._pending_selection is not None:
                # Only the player ACK commits the new local applied target.
                self.state = replace(self.state, applied=self._pending_selection,
                                     notice='', focus=0)
                # Success closes the menu behind a short success notice; a
                # power menu opened meanwhile then returns to the overview.
                if self.power_menu:
                    if self._power_return is not None and self._power_return[0] == 'targets':
                        self._power_return = None
                elif self.active and self.section == 'targets':
                    self.active = False
                    if self._notice_previous_active is not None:
                        self._notice_previous_active = False
                    self._show_notice(Notice('status.target_accepted', severity='success',
                                             priority=60,
                                             duration_seconds=ACCEPTED_NOTICE_SECONDS), now)
            else:
                self.state = replace(self.state, notice=result[1])
            self._pending_selection = None
            changed = True
        if self._notice_previous_active is not None and self.connect_notices.current(now=now) is None:
            self._restore_after_notice()
            changed = True
        if not self._discovery_started:
            # Once at service start, in the background.
            self._start_discovery(now)
        elif not self._has_targets() and now >= self._next_refresh:
            # Nothing found yet (e.g. network not ready at boot): retry quietly.
            self._start_discovery(now)
        return changed

    def _poll_network_setup(self):
        if self.network_setup is None:
            return False
        self.network_setup.tick()
        snap = self.network_setup.snapshot()
        changed = snap != self._netsetup_last
        self._netsetup_last = snap
        self.wifi_entry.sync(snap)
        if snap.state == 'idle' and self._netsetup_was_active is not None:
            # Setup over: back to what was shown before it started.
            self.active = self._netsetup_was_active
            self._netsetup_was_active = None
            changed = True
        elif snap.state != 'idle':
            self.active = True
        return changed

    def _poll_current_target(self, now):
        """Ask the player once (retry every few s) which speakers it has selected."""
        if self._current_loaded:
            return False
        with self._lock:
            result = self._current_result
            self._current_result = None
        if result is not None:
            if isinstance(result, frozenset):
                self._current_loaded = True
                # Never overwrite a selection the user has started or confirmed here.
                if (result and not self.state.applied and not self.state.draft
                        and not self._target_pending):
                    self.state = replace(self.state, applied=result, draft=result)
                    return True
                return False
            self._next_current_try = now + CURRENT_TARGET_RETRY_SECONDS
        if (self._current_worker is None or not self._current_worker.is_alive()) \
                and now >= self._next_current_try:
            reader = self._current_target
            def work():
                try:
                    value = frozenset(reader())
                except Exception:
                    value = False
                with self._lock:
                    self._current_result = value
            self._next_current_try = now + CURRENT_TARGET_RETRY_SECONDS
            self._current_worker = threading.Thread(target=work, name='ui-current-target', daemon=True)
            self._current_worker.start()
        return False

    def volume_overlay_active(self, now=None):
        now = time.monotonic() if now is None else now
        return self._volume_level is not None and now < self._volume_until

    def _show_shutdown_screen(self, screen, deadline=None):
        self.shutdown_screen = screen
        self._shutdown_frame = None
        self._shutdown_deadline = deadline
        self.active = True  # keep it on screen even when the player is gone

    def event(self, name, event, now=None):
        now = time.monotonic() if now is None else now
        if self.shutdown_screen is not None:
            return False  # shutting down or restarting: nothing else to do
        if (name, event) == ('power', 'long'):
            # The button service has already requested the power-off.
            self.power_menu = False
            self._show_shutdown_screen('transition', now + LONG_PRESS_SCREEN_SECONDS)
            return True
        if (name, event) == ('volume', 'no_target'):
            if self._power_busy or self.power_menu:
                return False
            current = self.connect_notices.current(now=now)
            if current is not None and current.key == 'status.volume_no_target':
                return True  # Do not restart an already displayed notice.
            self._show_notice(Notice('status.volume_no_target', priority=35,
                                     duration_seconds=2.0), now)
            return True
        if name == 'volume':
            # Display only: the level was already set and acknowledged by the player.
            if not (isinstance(event, str) and event.isdigit() and 0 <= int(event) <= 100):
                return False
            self._volume_level = int(event)
            self._volume_until = now + VOLUME_OVERLAY_SECONDS
            return True
        if (name, event) == ('speaker_select', 'pressed') and not self.power_menu and \
                self.netsetup_snapshot() is not None:
            # Short CONNECT during the setup: "Done" in the text entry, else nothing
            # (never the speaker selection behind the setup screen).
            return self.wifi_entry.short_connect(self.netsetup_snapshot())
        if (name, event) == ('speaker_select', 'pressed'):
            if self._power_busy or self.power_menu:
                return False
            return self._open_targets(now)
        if name == 'connect':
            return self._connect_event(event, now)
        netsetup = self.netsetup_snapshot()
        if netsetup is not None and not self.power_menu and (name, event) != ('power', 'short'):
            # The setup screen stays until done, timed out or cancelled; the
            # encoder drives the on-device choice (D-224).
            if name in ('encoder', 'encoder_sw'):
                return self.wifi_entry.event(name, event, now, netsetup)
            return False
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
                changed_language = self.languages[self.language_focus] != self.language
                self.language = self.languages[self.language_focus]
                self.renderer = MultiRenderer(language=self.language)
                self.language_picker = False
                if changed_language and self._on_language_change is not None:
                    try:
                        self._on_language_change(self.language)
                    except Exception:
                        pass  # persistence is best effort; the menu keeps working
                self.state = replace(self.state, notice='')
                # Rebuild localized fallback names after a user language switch.
                self._start_discovery(now)
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

    def semantic_screen(self, *, now):
        """Read-only semantic snapshot of the active non-WLAN menu.

        Navigation and selectable overlays share this snapshot with every
        renderer. WLAN text input and hotspot presentation still need their
        specialised projections; they are deliberately not duplicated here.
        No encoder dispatch, player writes or mutations occur.
        """
        from .character_adapters import (
            navigation_screen, speaker_selection_screen, power_menu_screen,
            language_menu_screen, network_status_screen,
        )
        from .semantic_screen import ScreenItem, SemanticScreen, screen_from_notice

        if self.shutdown_screen is not None:
            keys = {
                'transition': 'system.shutting_down',
                'wait': 'system.shutting_down',
                'reboot': 'system.restarting',
                'final': 'system.please_wait',
                'safe': 'system.safe_to_unplug',
                'start': 'system.starting',
            }
            try:
                key = keys[self.shutdown_screen]
            except KeyError as exc:
                raise ValueError('unknown shutdown screen') from exc
            return SemanticScreen('notice', message=Translator(self.language).tr(key),
                                  severity='info')

        notice = (self.connect_notices.current(now=now)
                  if self._notice_previous_active is not None else None)
        if notice is not None:
            screen = screen_from_notice(notice, language=self.language)
            if NoticeTiming().encoder_can_confirm(notice):
                return SemanticScreen('notice', message=screen.message + '\\n' +
                                      Translator(self.language).tr('notice.dismiss_hint'),
                                      severity=screen.severity)
            return screen

        # WLAN setup has higher precedence than power, except for the
        # existing power-menu override. Its screen remains in the D-224 adapter.
        if self.netsetup_snapshot() is not None and not self.power_menu:
            return None

        if self.power_menu:
            return power_menu_screen(self.power_focus, language=self.language)
        if self.volume_overlay_active(now=now):
            return SemanticScreen('idle', items=(
                ScreenItem('volume', Translator(self.language).tr('menu.volume'),
                           value=str(self._volume_level)),))
        if self.language_picker:
            return language_menu_screen(tuple(self.languages), self.language_focus,
                                        language=self.language)
        if not self.active:
            return None
        if self.section in NAVIGATION_PAGES:
            return navigation_screen(self.section, self.menu_focus,
                                     language=self.language)
        if self.section == 'network_status':
            return network_status_screen(self.network_snapshot, language=self.language)
        if self.section == 'targets':
            if self._target_pending:
                return screen_from_notice(Notice('status.target_pending'),
                                          language=self.language)
            if self.state.notice and self.state.notice != 'menu.back':
                return screen_from_notice(Notice(self.state.notice),
                                          language=self.language)
            return speaker_selection_screen(self.state, language=self.language)
        raise ValueError('unknown active menu section')

    def frame(self, image_to_frame):
        if self.shutdown_screen is not None:
            if self._shutdown_frame is None:
                from system_screen import screen_image
                self._shutdown_frame = image_to_frame(screen_image(self.shutdown_screen, self.language))
            return self._shutdown_frame
        active_notice = (self.connect_notices.current(now=time.monotonic())
                         if self._notice_previous_active is not None else None)
        if active_notice is not None:
            return image_to_frame(self._connect_renderer.render(
                active_notice, language=self.language, grayscale=True,
                dismiss_hint=NoticeTiming().encoder_can_confirm(active_notice)))
        netsetup = self.netsetup_snapshot()
        if netsetup is not None and not self.power_menu:
            return image_to_frame(self.wifi_entry.frame(self.renderer, netsetup,
                                                        now=time.monotonic()))
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
            for slot, index in enumerate(self.renderer.menu_window(len(self.languages), self.language_focus)):
                label=self.renderer.translator.language_name(self.languages[index])
                self.renderer.menu_row(im, slot, label, selected=index==self.language_focus)
            self.renderer.bottom(im,status=self.renderer.tr('status.local_only'),
                                 footer=self.renderer.tr('footer.confirm'))
            return image_to_frame(im)
        if self.section in NAVIGATION_PAGES:
            heading, items = NAVIGATION_PAGES[self.section]
            return image_to_frame(self._navigation_renderer.page(
                heading, items, self.menu_focus, language=self.language))
        if self.section == 'network_status':
            return image_to_frame(self._navigation_renderer.network_status(
                self.network_snapshot, language=self.language))
        return image_to_frame(self.renderer.render_multi(self.state,gray=True,
                                                         searching=self.searching()))
