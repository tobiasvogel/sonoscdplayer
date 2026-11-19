"""On-device WLAN choice during the setup: list, name and password entry.

UI state only. NetworkManager work happens in ``network_setup.SetupSession``;
this controller turns encoder input into session calls and draws the screens.
"""
from __future__ import annotations

from sonoscdplayer.ui.menu.text_input import DEFAULT_EXTRA_CHARACTERS, TextEntry
from sonoscdplayer.ui.renderers.oled_128_text_input import render_text_entry

RESCAN = object()
HIDDEN = object()
BACK = object()
HINT_SWITCH_SECONDS = 4.0   # hotspot screen alternates its two footer hints
MIN_PASSWORD = 8
MAX_PASSWORD = 63
MAX_SSID_BYTES = 32


def _password_check(allow_empty):
    def check(text):
        if text == '' and allow_empty:
            return ''
        if len(text) < MIN_PASSWORD:
            return ('textinput.too_short', {'count': MIN_PASSWORD})
        return ''
    return check


def _ssid_check(text):
    return '' if text else ('textinput.empty', {})


class WifiEntryController:
    def __init__(self, session, *, extra_characters=DEFAULT_EXTRA_CHARACTERS):
        self.session = session
        self.extra_characters = extra_characters
        self.mode = None        # None, 'list', 'ssid', 'password'
        self.focus = 0
        self.entry = None
        self.ssid_entry = None
        self.target = ''
        self.hidden = False
        self.list_notice = ''
        self._submitted = None  # 'saved', 'password' or 'open'

    # ---- state following the session ------------------------------------
    def sync(self, snap):
        """Follow the session; reopen the entry after a failed connection."""
        if snap.state not in ('scanning', 'choosing', 'connecting'):
            self._reset()
            return
        if snap.state == 'choosing' and self.mode is None:
            self.mode, self.focus = 'list', 0
        # A submission came back as failed (the UI may never see 'connecting').
        if self._submitted is not None and snap.state == 'choosing' and \
                snap.error == 'connect_failed':
            if self._submitted == 'saved':
                # The stored password no longer fits: ask for it.
                self._open_password(error=('netsetup.saved_failed', {}))
            elif self._submitted == 'password' and self.entry is not None:
                self.mode = 'password'
                self.entry.reopen(error=('netsetup.failed', {}))
            else:
                self.mode = 'list'
                self.list_notice = 'netsetup.failed'
            self._submitted = None

    def _reset(self):
        self.mode = None
        self.entry = self.ssid_entry = None
        self.list_notice = ''
        self._submitted = None

    def items(self, snap):
        return list(snap.networks) + [RESCAN, HIDDEN, BACK]

    # ---- input -------------------------------------------------------------
    def event(self, name, event, now, snap):
        if snap.state == 'hotspot':
            if (name, event) == ('encoder_sw', 'pressed'):
                return self.session.manual()
            return False
        if snap.state != 'choosing' or self.mode is None:
            return False
        self.session.touch()
        step = {'right': 1, 'left': -1}.get(event) if name == 'encoder' else None
        pressed = (name, event) == ('encoder_sw', 'pressed')
        if self.mode == 'list':
            items = self.items(snap)
            if step:
                self.focus = max(0, min(len(items) - 1, self.focus + step))
                self.list_notice = ''
                return True
            if pressed:
                return self._choose(items[self.focus], snap)
            return False
        if step:
            return self.entry.turn(step, now=now)
        if not pressed:
            return False
        self.entry.press()
        return self._after_entry()

    def short_connect(self, snap):
        """Short CONNECT press during name/password entry = Done."""
        if snap.state != 'choosing' or self.mode not in ('ssid', 'password'):
            return False
        self.session.touch()
        self.entry.finish()
        return self._after_entry()

    def _after_entry(self):
        if self.entry.result == 'cancelled':
            self.mode, self.entry = 'list', None
        elif self.entry.result == 'done' and self.mode == 'ssid':
            self.ssid_entry = self.entry
            self.target, self.hidden = self.entry.text, True
            self._open_password(allow_empty=True)
        elif self.entry.result == 'done':
            self._submitted = 'password'
            self.session.connect_manual(self.target, self.entry.text, hidden=self.hidden)
        return True

    def _choose(self, item, snap):
        self.list_notice = ''
        if item is BACK:
            self._reset()
            return self.session.back_to_hotspot()
        if item is RESCAN:
            self.focus = 0
            return self.session.rescan()
        if item is HIDDEN:
            self.mode = 'ssid'
            self.entry = TextEntry(extra_characters=self.extra_characters,
                                   max_bytes=MAX_SSID_BYTES, validate=_ssid_check)
            return True
        self.target, self.hidden = item.ssid, False
        if item.ssid in snap.saved:
            # Known network: connect without asking; ask only if that fails.
            self._submitted = 'saved'
            self.session.connect_manual(item.ssid, None)
        elif not item.secure:
            self._submitted = 'open'
            self.session.connect_manual(item.ssid, '')
        else:
            self._open_password()
        return True

    def _open_password(self, *, allow_empty=False, error=None):
        self.mode = 'password'
        self.entry = TextEntry(max_bytes=MAX_PASSWORD, validate=_password_check(allow_empty))
        self.entry.error = error

    # ---- drawing -------------------------------------------------------------
    def frame(self, renderer, snap, *, now, gray=True):
        if snap.state == 'hotspot':
            phase = int(now // HINT_SWITCH_SECONDS) % 2
            footer = renderer.tr('netsetup.manual_hint' if phase == 0
                                 else 'netsetup.cancel_hint')
            return renderer.render_netsetup(snap, gray=gray, footer=footer)
        if snap.state == 'choosing' and self.mode == 'list':
            return renderer.render_wifi_list(snap.networks, snap.saved, self.focus,
                                             notice=self.list_notice, gray=gray)
        if snap.state == 'choosing' and self.mode == 'ssid':
            return render_text_entry(renderer, self.entry, title=renderer.tr('netsetup.ssid_title'),
                                     gray=gray)
        if snap.state == 'choosing' and self.mode == 'password':
            return render_text_entry(renderer, self.entry,
                                     title=renderer.tr('netsetup.password_title'),
                                     subtitle=self.target, gray=gray)
        return renderer.render_netsetup(snap, gray=gray)
