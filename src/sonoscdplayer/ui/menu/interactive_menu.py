"""The device menu: speaker selection, settings, network, power.

Used by the display service. Speaker searches run in a background thread,
so the display stays responsive. The menu itself never talks to Sonos: a
confirmed speaker selection is sent to the player service, which checks it.
"""
from __future__ import annotations
import threading
from dataclasses import replace
import time
from sonoscdplayer.ui.menu.speakers.multi_target import MultiSelection, discover_targets
from sonoscdplayer.ui.renderers.oled_128_speakers import MultiRenderer
from sonoscdplayer.ui.i18n import Translator, available_languages
from sonoscdplayer.ui.menu.speakers.target_rpc_client import rejection_notice, loggable
from sonoscdplayer.ui.menu import play_feedback
from sonoscdplayer.ui.menu.speakers.selection_plan import plan_from_applied
from sonoscdplayer.ui.menu.connect_notice import ConnectNoticeView
from sonoscdplayer.ui.notices.notice import Notice
from sonoscdplayer.ui.notices.render import NoticeRenderer
from sonoscdplayer.ui.notices.policy import NoticeTiming
from sonoscdplayer.ui.menu.text_input import DEFAULT_EXTRA_CHARACTERS
from sonoscdplayer.ui.menu.wifi_entry import WifiEntryController
from sonoscdplayer.ui.menu.navigation_pages import NavigationRenderer
from sonoscdplayer.ui.menu.menu_catalog import NAVIGATION_PAGES, POWER_ITEMS
from sonoscdplayer.ui.menu.network_status import NetworkStatus, read_network_status


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
# how long an acknowledged volume level stays visible.
VOLUME_OVERLAY_SECONDS = 2.0


class InteractiveMenu:
    """The menu state machine: screens, focus, speaker selection, settings.

    The display service creates one InteractiveMenu, feeds it button and
    encoder events with ``event()`` and draws ``frame()``. Actions that need
    the player go through the given callbacks (``target_rpc``,
    ``power_action``, ``disc_reset``, ...).
    """
    def __init__(self, *, discover=discover_targets, refresh_seconds=15.0, language='de', target_rpc=None, group_target_rpc=None, power_action=None, network_reader=read_network_status, current_target=None, on_language_change=None, network_setup=None, text_extra_characters=DEFAULT_EXTRA_CHARACTERS, disc_reset=None):
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
        # disc_reset() -> True: forget the inserted CD's metadata;
        # runs on a worker thread, never on the display loop.
        self._disc_reset = disc_reset
        self._disc_reset_busy = False
        self._disc_reset_result = None
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
        # WLAN setup through the hotspot; None = not available here.
        self.network_setup = network_setup
        # On-device WLAN choice from the hotspot screen.
        self.wifi_entry = (WifiEntryController(network_setup,
                                               extra_characters=text_extra_characters)
                           if network_setup is not None else None)
        self._netsetup_last = None
        self._netsetup_was_active = None
        self.player_mode = None   # updated by the OLED loop from the player status
        self._play_pending = False        # PLAY pressed, no answer yet
        self._rejection_seq = None        # last PLAY rejection already seen
        self._target_rev = None           # player's selection version already seen
        self._selection_reload = False    # re-read the selection, it changed elsewhere
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
            elif action == 'menu.reset_disc':
                self.section = 'reset_disc'
                self.menu_focus = 0                     # "Back" preselected
            elif action == 'menu.back':
                self.section = 'main'
                self.menu_focus = NAVIGATION_PAGES['main'][1].index('menu.settings')
            else:
                return False
        elif section == 'reset_disc':
            back_to = NAVIGATION_PAGES['settings'][1].index('menu.reset_disc')
            if action == 'menu.reset_disc_confirm':
                self._start_disc_reset(now)
            elif action != 'menu.back':
                return False
            self.section = 'settings'
            self.menu_focus = back_to
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

    def _start_disc_reset(self, now):
        if self._disc_reset is None:
            self._show_notice(Notice('status.disc_reset_failed', severity='warning',
                                     duration_seconds=SCAN_RESULT_SECONDS), now)
            return
        if self._disc_reset_busy:
            return
        self._disc_reset_busy = True

        def work():
            try:
                ok = self._disc_reset() is True
            except Exception:
                ok = False
            with self._lock:
                self._disc_reset_result = ok
        threading.Thread(target=work, name='ui-disc-reset', daemon=True).start()

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
        """State of the WLAN setup, or None if it is not running."""
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

    def _play_event(self, event, now):
        """PLAY/PAUSE was pressed: say that playback starts, or why not."""
        if self._power_busy or self.power_menu or self.netsetup_snapshot() is not None:
            return False
        if event == 'requested':
            if self.player_mode not in (None, 'STOPPED', 'ERROR'):
                return False                    # a pause or resume needs no notice
            self._play_pending = True
            self._show_notice(Notice('play.requested', priority=45,
                                     duration_seconds=play_feedback.REQUEST_SECONDS), now)
            return True
        self._play_pending = False
        key = 'play.no_target' if event == 'no_target' else 'play.blocked'
        self._show_notice(Notice(key, severity='warning', priority=45,
                                 duration_seconds=play_feedback.REJECTION_SECONDS), now)
        return True

    def player_status(self, state, now=None):
        """Take the player's status from the display loop; True if the screen changed."""
        now = time.monotonic() if now is None else now
        player = state.get('player') if isinstance(state, dict) else None
        self.player_mode = player.get('mode') if isinstance(player, dict) else None
        changed = False
        if self._play_pending and self.player_mode in ('STARTING', 'PLAYING', 'PAUSED'):
            self._play_pending = False
            current = self.connect_notices.current(now=now)
            if current is not None and current.key == 'play.requested':
                self.connect_notices.clear(now=now)
                self._restore_after_notice()
                changed = True
        rev = state.get('target_rev') if isinstance(state, dict) else None
        if isinstance(rev, int):
            if (self._target_rev is not None and rev != self._target_rev
                    and self._current_target is not None):
                # The selection changed (e.g. in the Sonos app): read it again
                # and search, so the list shows the new groups.
                self._selection_reload = True
                self._current_loaded = False
                self._next_current_try = 0.0
                self._start_discovery(now)
            self._target_rev = rev
        rejection = state.get('last_rejection') if isinstance(state, dict) else None
        seq = rejection.get('seq') if isinstance(rejection, dict) else None
        if seq is None:
            if self._rejection_seq is None:
                self._rejection_seq = 0
        elif self._rejection_seq is None:
            self._rejection_seq = seq           # from before this display started
        elif seq != self._rejection_seq:
            self._rejection_seq = seq
            self._play_pending = False
            if not (self._power_busy or self.power_menu):
                self._show_notice(Notice(play_feedback.rejection_notice(rejection.get('reason')),
                                         severity='warning', priority=45,
                                         duration_seconds=play_feedback.REJECTION_SECONDS), now)
                changed = True
        return changed

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
            choice = POWER_ITEMS[self.power_focus].removeprefix('menu.')
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
                from sonoscdplayer.ui.menu.speakers.multi_target import Topology
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
            except Exception as exc:
                result = rejection_notice(exc)
                print(f'[ui] speaker selection rejected: {loggable(exc)}', flush=True)
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
            reset_result = self._disc_reset_result
            self._disc_reset_result = None
        if reset_result is not None:
            self._disc_reset_busy = False
            self._show_notice(Notice('status.disc_reset_done' if reset_result else
                                     'status.disc_reset_failed',
                                     severity='success' if reset_result else 'warning',
                                     duration_seconds=SCAN_RESULT_SECONDS), now)
            changed = True
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
                reload, self._selection_reload = self._selection_reload, False
                editing = self.state.draft != self.state.applied or self._target_pending
                if reload and not editing:
                    # Changed elsewhere (Sonos app): show the player's selection.
                    if result != self.state.applied:
                        self.state = replace(self.state, applied=result, draft=result)
                        return True
                    return False
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
        """True while the volume overlay is shown."""
        now = time.monotonic() if now is None else now
        return self._volume_level is not None and now < self._volume_until

    def _show_shutdown_screen(self, screen, deadline=None):
        self.shutdown_screen = screen
        self._shutdown_frame = None
        self._shutdown_deadline = deadline
        self.active = True  # keep it on screen even when the player is gone

    def event(self, name, event, now=None):
        """Handle one input event; True if the screen changed."""
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
        if name == 'play':
            return self._play_event(event, now)
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
            # encoder drives the on-device choice.
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
        """What the display shows now, for every display type.

        This is the ONLY place that decides which screen wins:
        shutdown > notice > WLAN setup > POWER > volume > language > menu.
        WLAN setup returns None (its screens have their own renderer),
        as does an inactive menu without overlay. Read-only: no input,
        no player writes.
        """
        from sonoscdplayer.ui.screens import semantic_views as views

        if self.shutdown_screen is not None:
            return views.system_screen(self.shutdown_screen, language=self.language)

        notice = (self.connect_notices.current(now=now)
                  if self._notice_previous_active is not None else None)
        if notice is not None:
            return views.notice_screen(
                notice, language=self.language,
                dismissable=NoticeTiming().encoder_can_confirm(notice))

        # POWER is the only overlay that may cover the WLAN setup.
        if self.netsetup_snapshot() is not None and not self.power_menu:
            return None
        if self.power_menu:
            return views.power_menu_screen(self.power_focus, notice=self.power_notice,
                                           language=self.language)
        if self.volume_overlay_active(now=now):
            return views.volume_screen(self._volume_level, language=self.language)
        if self.language_picker:
            return views.language_menu_screen(tuple(self.languages), self.language_focus,
                                              language=self.language)
        if not self.active:
            return None
        if self.section in NAVIGATION_PAGES:
            return views.navigation_screen(self.section, self.menu_focus,
                                           language=self.language)
        if self.section == 'network_status':
            return views.network_status_screen(self.network_snapshot,
                                               language=self.language)
        if self.section == 'targets':
            screen = views.speaker_selection_screen(self.state, language=self.language,
                                                    searching=self.searching())
            # Speaker feedback: the OLED keeps the list and shows it in the
            # status line; small displays show it instead of the list.
            if self._target_pending:
                return views.with_feedback(screen, Notice('status.target_pending'),
                                           language=self.language)
            if self.state.notice and self.state.notice != 'menu.back':
                return views.with_feedback(screen, Notice(self.state.notice),
                                           language=self.language)
            return screen
        raise ValueError('unknown active menu section')

    def frame(self, image_to_frame, *, gray=True):
        """The current screen as display frame (via ``image_to_frame``), or None."""
        if self.shutdown_screen is not None:
            if self._shutdown_frame is None:
                from sonoscdplayer.ui.system_screens import screen_image
                self._shutdown_frame = image_to_frame(screen_image(
                    self.shutdown_screen, self.language, grayscale=gray))
            return self._shutdown_frame
        from sonoscdplayer.ui.renderers.oled_128_screens import graphic_frame_from_menu
        return image_to_frame(graphic_frame_from_menu(self, now=time.monotonic(), gray=gray))
