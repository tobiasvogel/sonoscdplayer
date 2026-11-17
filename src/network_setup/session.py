"""State machine of one WLAN setup (D-223, D-224).

idle -> preparing -> hotspot -> connecting -> connected
                        ^            |
                        +-- failed --+   (hotspot restarts, same password)
hotspot -> idle after 15 min or on cancel (CONNECT held again).

On-device entry (encoder press on the hotspot screen, D-224):
hotspot -> scanning -> choosing -> connecting -> connected
              (hotspot ends)  ^         |
                              +-failed--+   (back to the list/password entry)
choosing -> preparing -> hotspot ("Back"); choosing -> idle after 15 min
without input. The list, password entry etc. are UI state in the menu.
All NetworkManager work runs in worker threads; the UI only reads snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
import secrets
import threading
import time

from .nm import NmError, valid_password, valid_ssid

HOTSPOT_SECONDS = 15 * 60
CONNECTED_SHOW_SECONDS = 6.0
HANDOVER_SECONDS = 2.0
RADIO_SETTLE_SECONDS = 1.0   # after the hotspot ends, before scanning again
SSID_PREFIX = 'SonosCDPlayer-'
# No 0/O, 1/I/L: the password is read off a small display.
PASSWORD_ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
PASSWORD_LENGTH = 8


def new_password():
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


@dataclass(frozen=True)
class SetupSnapshot:
    # idle, preparing, hotspot, scanning, choosing, connecting, connected, unavailable
    state: str = 'idle'
    hotspot_ssid: str = ''
    password: str = ''
    address: str = ''
    url: str = ''
    target: str = ''             # WLAN being connected / connected to
    error: str = ''              # '', 'connect_failed', 'hotspot_failed', 'no_wifi'
    remaining: int = 0           # seconds until the hotspot ends
    networks: tuple = ()
    manual: bool = False         # on-device entry instead of the web page
    saved: tuple = ()            # SSIDs with a stored profile (on-device entry)


class SetupSession:
    def __init__(self, nm, *, clock=time.monotonic, web_factory=None,
                 password_factory=new_password, hotspot_seconds=HOTSPOT_SECONDS, log=print,
                 sleep=time.sleep):
        self._nm = nm
        self._clock = clock
        self._web_factory = web_factory
        self._password_factory = password_factory
        self._hotspot_seconds = hotspot_seconds
        self._log = log
        self._lock = threading.RLock()
        self._snap = SetupSnapshot()
        self._device = None
        self._deadline = 0.0
        self._shown_until = 0.0
        self._web = None
        self._worker = None
        self._generation = 0
        self._sleep = sleep
        self._identity = None        # (hotspot ssid, password, networks)

    # ---- read side -------------------------------------------------------
    def snapshot(self):
        with self._lock:
            snap = self._snap
            if snap.state in ('hotspot', 'choosing'):
                remaining = max(0, int(self._deadline - self._clock()))
                snap = SetupSnapshot(**{**snap.__dict__, 'remaining': remaining})
            return snap

    @property
    def active(self):
        return self.snapshot().state != 'idle'

    # ---- control ---------------------------------------------------------
    def start(self):
        """Begin a setup (CONNECT 10 s / menu). Ignored while one is running."""
        with self._lock:
            if self._snap.state not in ('idle', 'unavailable'):
                return False
            self._generation += 1
            generation = self._generation
            self._snap = SetupSnapshot(state='preparing')
        self._spawn(self._prepare, generation)
        return True

    def cancel(self):
        """End the setup now; NetworkManager reconnects known networks itself."""
        with self._lock:
            if self._snap.state in ('idle',):
                return False
            self._generation += 1
            self._snap = SetupSnapshot()
        self._spawn(self._teardown, None)
        return True

    def submit(self, ssid, password):
        """Called by the web page. Returns an error key or ''."""
        if not valid_ssid(ssid):
            return 'invalid_ssid'
        if not valid_password(password):
            return 'invalid_password'
        with self._lock:
            if self._snap.state != 'hotspot':
                return 'not_ready'
            self._generation += 1
            generation = self._generation
            hidden = ssid not in {n.ssid for n in self._snap.networks}
            self._snap = SetupSnapshot(**{**self._snap.__dict__, 'state': 'connecting',
                                          'target': ssid, 'error': ''})
        self._spawn(self._connect, generation, ssid, password, hidden)
        return ''

    # ---- on-device entry (D-224) ----------------------------------------
    def manual(self):
        """End the hotspot and offer the network list on the display."""
        with self._lock:
            if self._snap.state != 'hotspot':
                return False
            self._generation += 1
            generation = self._generation
            previous = self._snap.networks
            self._snap = SetupSnapshot(state='scanning', manual=True)
        self._spawn(self._scan_manual, generation, previous)
        return True

    def back_to_hotspot(self):
        """From the on-device list back to the hotspot (same name and password)."""
        with self._lock:
            if self._snap.state != 'choosing' or self._identity is None:
                return False
            self._generation += 1
            generation = self._generation
            self._snap = SetupSnapshot(state='preparing')
        self._spawn(self._restart_hotspot, generation)
        return True

    def connect_manual(self, ssid, password=None, *, hidden=False):
        """Connect from the display; password None uses a stored profile."""
        if not valid_ssid(ssid):
            return 'invalid_ssid'
        if password is not None and not valid_password(password):
            return 'invalid_password'
        with self._lock:
            if self._snap.state != 'choosing':
                return 'not_ready'
            self._generation += 1
            generation = self._generation
            self._snap = SetupSnapshot(**{**self._snap.__dict__, 'state': 'connecting',
                                          'target': ssid, 'error': ''})
        self._spawn(self._connect_manual, generation, ssid, password, hidden)
        return ''

    def touch(self):
        """Input on the display: the 15 minutes start again."""
        with self._lock:
            if self._snap.state == 'choosing':
                self._deadline = self._clock() + self._hotspot_seconds

    def tick(self):
        """Call regularly (UI loop): ends an unused hotspot, hides 'connected'."""
        now = self._clock()
        with self._lock:
            state = self._snap.state
            if state in ('hotspot', 'choosing') and now >= self._deadline:
                self._log('[netsetup] setup timed out')
                expired = True
            elif state in ('connected', 'unavailable') and now >= self._shown_until:
                self._snap = SetupSnapshot()
                return
            else:
                return
        if expired:
            self.cancel()

    # ---- workers ---------------------------------------------------------
    def join(self, timeout=5.0):
        """Wait for the current worker (tests, orderly shutdown)."""
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout)

    def _spawn(self, target, *args):
        worker = threading.Thread(target=target, args=args, name='netsetup', daemon=True)
        with self._lock:
            self._worker = worker
        worker.start()

    def _current(self, generation):
        with self._lock:
            return generation == self._generation

    def _prepare(self, generation):
        try:
            device = self._nm.wifi_device()
            if device is None:
                with self._lock:
                    if self._current(generation):
                        self._snap = SetupSnapshot(state='unavailable', error='no_wifi')
                        self._shown_until = self._clock() + CONNECTED_SHOW_SECONDS
                return
            self._device = device
            # Scan first: the WLAN chip cannot scan while it is the hotspot.
            networks = tuple(self._nm.scan(device))
            ssid = SSID_PREFIX + self._nm.hardware_suffix(device)
            password = self._password_factory()
            self._start_hotspot(generation, ssid, password, networks, error='')
        except NmError as exc:
            self._log(f'[netsetup] {exc}')
            with self._lock:
                if self._current(generation):
                    self._snap = SetupSnapshot(state='unavailable', error='hotspot_failed')
                    self._shown_until = self._clock() + CONNECTED_SHOW_SECONDS
            self._stop_web()

    def _start_hotspot(self, generation, ssid, password, networks, *, error):
        self._identity = (ssid, password, networks)
        self._nm.start_hotspot(self._device, ssid, password)
        address = self._nm.hotspot_address(self._device)
        url = self._start_web(address)
        with self._lock:
            if not self._current(generation):
                stale = True
            else:
                stale = False
                self._deadline = self._clock() + self._hotspot_seconds
                self._snap = SetupSnapshot(state='hotspot', hotspot_ssid=ssid, password=password,
                                           address=address, url=url, error=error,
                                           networks=networks)
        if stale:  # cancelled meanwhile
            self._teardown(None)
        else:
            self._log('[netsetup] hotspot active')

    def _connect(self, generation, ssid, password, hidden=False):
        snap = self.snapshot()
        self._sleep(HANDOVER_SECONDS)  # let the answer page reach the phone first
        self._stop_web()
        try:
            self._nm.stop_hotspot()
            self._nm.connect(self._device, ssid, password, hidden=hidden)
        except NmError as exc:
            self._log(f'[netsetup] {exc}')
            if not self._current(generation):
                return
            try:  # back to the hotspot with the same name and password
                self._start_hotspot(generation, snap.hotspot_ssid, snap.password,
                                    snap.networks, error='connect_failed')
            except NmError as again:
                self._log(f'[netsetup] {again}')
                with self._lock:
                    if self._current(generation):
                        self._snap = SetupSnapshot(state='unavailable', error='hotspot_failed')
                        self._shown_until = self._clock() + CONNECTED_SHOW_SECONDS
            return
        with self._lock:
            if self._current(generation):
                self._snap = SetupSnapshot(state='connected', target=ssid)
                self._shown_until = self._clock() + CONNECTED_SHOW_SECONDS
        self._log('[netsetup] connected to the new network')

    def _failed(self, generation, error):
        with self._lock:
            if self._current(generation):
                self._snap = SetupSnapshot(state='unavailable', error=error)
                self._shown_until = self._clock() + CONNECTED_SHOW_SECONDS

    def _scan_manual(self, generation, previous):
        self._stop_web()
        try:
            self._nm.stop_hotspot()
        except NmError as exc:
            self._log(f'[netsetup] {exc}')
        self._sleep(RADIO_SETTLE_SECONDS)
        # Right after the hotspot a scan may come back empty: keep the old list then.
        networks = tuple(self._nm.scan(self._device)) or previous
        saved = tuple(sorted(self._nm.saved_profiles()))
        with self._lock:
            if self._current(generation):
                self._deadline = self._clock() + self._hotspot_seconds
                self._snap = SetupSnapshot(state='choosing', manual=True,
                                           networks=networks, saved=saved)

    def _restart_hotspot(self, generation):
        ssid, password, networks = self._identity
        try:
            self._start_hotspot(generation, ssid, password, networks, error='')
        except NmError as exc:
            self._log(f'[netsetup] {exc}')
            self._failed(generation, 'hotspot_failed')
            self._stop_web()

    def _connect_manual(self, generation, ssid, password, hidden):
        try:
            if password is None:
                self._nm.connect_saved(self._device, ssid)
            else:
                self._nm.connect(self._device, ssid, password, hidden=hidden)
        except NmError as exc:
            self._log(f'[netsetup] {exc}')
            with self._lock:
                if self._current(generation):
                    self._deadline = self._clock() + self._hotspot_seconds
                    self._snap = SetupSnapshot(**{**self._snap.__dict__, 'state': 'choosing',
                                                  'error': 'connect_failed'})
            return
        with self._lock:
            if self._current(generation):
                self._snap = SetupSnapshot(state='connected', target=ssid)
                self._shown_until = self._clock() + CONNECTED_SHOW_SECONDS
        self._log('[netsetup] connected to the chosen network')

    def _teardown(self, _unused):
        self._stop_web()
        try:
            self._nm.stop_hotspot()
        except NmError as exc:
            self._log(f'[netsetup] {exc}')

    # ---- web page ----------------------------------------------------------
    def _start_web(self, address):
        self._stop_web()
        if self._web_factory is None:
            return f'http://{address}'
        web = self._web_factory(self, address)
        with self._lock:
            self._web = web
        return web.url

    def _stop_web(self):
        with self._lock:
            web, self._web = self._web, None
        if web is not None:
            web.stop()
