"""Notice disc changes while the player is idle.

The player service calls DiscMonitor.poll() regularly. It looks at the drive
only while nothing plays (no reader, stream or unresolved Sonos group), and
reads the table of contents and CD-TEXT when a new disc appears. While the
player plays or the drive sleeps, only media_removed() asks the drive status
(tray open / no disc), so a CD taken out with the drive's own button is
noticed at once. If the USB
drive reappears under another /dev/srN, it is used again only if exactly one
drive with the same USB identity (vendor, product, serial) is found.
No Sonos commands are sent from here.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from sonoscdplayer.drive.disc_startup import (drive_media_status, CDS_NO_DISC, CDS_TRAY_OPEN,
                          CDS_DRIVE_NOT_READY, CDS_DISC_OK)
from sonoscdplayer.drive.disc_state import DiscMode, DiscStatus
from sonoscdplayer.drive.drive_lock import drive_lock
from sonoscdplayer.drive.usb_identity import usb_identity, recovered_device
from sonoscdplayer.player.state import Mode


def idle_for_disc_io(controller) -> bool:
    """True if the player is idle enough to look at the drive."""
    return (controller.state.mode in (Mode.NO_DISC, Mode.STOPPED, Mode.ERROR)
            and not controller.owned_url
            and controller.player is None
            and not controller.server.alive()
            and not controller.server.reader_active()
            and not controller._joined
            and not controller._group_uncertain)


def _toc_key(disc):
    """Hashable identity of a TOC (DiscTOC is hashable; test doubles may not be)."""
    try:
        hash(disc)
        return disc
    except TypeError:
        return repr(disc)


class DiscMonitor:
    """Watches one drive for disc changes; only the service worker calls poll()."""

    def __init__(self, device, identity, *, media=drive_media_status,
                 toc=None, path_exists=None, identity_reader=usb_identity,
                 lock=drive_lock, recover=recovered_device, on_rebind=None,
                 cdtext=None):
        from sonoscdplayer.audio.table_of_contents import read_toc
        self.device = device
        self.identity = identity
        self.media = media
        self.read_toc = toc if toc is not None else read_toc
        self.path_exists = path_exists if path_exists is not None else lambda p: Path(p).exists()
        self.identity_reader = identity_reader
        self.lock = lock
        self.recover = recover
        self.on_rebind = on_rebind
        self._disconnected = False
        self._disconnect_error = None
        self.loaded_toc = None
        # Optional CD-TEXT reader (device -> read_cdtext() tuple). Runs only
        # here, under the drive lock right after the TOC, never during playback.
        self.read_cdtext = cdtext
        self.loaded_cdtext = None
        # CD-TEXT is read in the background after READY (cd-info can take
        # up to its timeout, or hang on some discs); PLAY cancels it.
        self.cdtext_pending = False
        self._cdtext_thread = None
        self._cdtext_proc = None
        self._cdtext_cancel = threading.Event()
        self._cdtext_hangs = set()      # TOCs on which cd-info timed out
        # TOC of another CD found by a wake verification (see wake_validation).
        self.changed_disc = None

    def poll(self, controller) -> bool:
        """Apply a verified idle observation; return whether status changed.

        A missing device clears the previous TOC but is never replaced by a
        different device at this phase. A drive busy under our shared lock
        retains its last observation and cannot start a concurrent query.
        """
        if not idle_for_disc_io(controller):
            return False

        if not self.path_exists(self.device):
            # A new device may appear under the same /dev/srN or a new one.
            # First invalidate the stale TOC; only a later poll can rebind.
            if not self._disconnected:
                return self._mark_disconnected(controller)
            return self._recover_or_hold(controller)

        current_id = self.identity_reader(self.device)
        if self._disconnected:
            return self._recover_or_hold(controller)
        if self.identity is not None and current_id != self.identity:
            return self._mark_disconnected(
                controller, 'drive reconnect requires verified identity')

        with self.lock(self.device, blocking=False) as acquired:
            if not acquired:
                return False
            try:
                code = self.media(self.device)
            except (OSError, ValueError) as exc:
                return self._apply(controller, DiscStatus(
                    DiscMode.UNREADABLE, device=self.device,
                    error=f'media status: {type(exc).__name__}: {exc}'))

            if code == CDS_NO_DISC:
                return self._apply(controller, DiscStatus(DiscMode.NO_DISC, self.device))
            if code == CDS_TRAY_OPEN:
                return self._apply(controller, DiscStatus(DiscMode.EJECTED, self.device))
            if code == CDS_DRIVE_NOT_READY:
                return self._apply(controller, DiscStatus(DiscMode.LOADING, self.device))
            if code != CDS_DISC_OK:
                return self._apply(controller, DiscStatus(
                    DiscMode.UNREADABLE, device=self.device, error='unknown CDROM drive status'))

            # No repeated cdparanoia scans for an already verified disc,
            # unless a wake found that another CD was put in meanwhile.
            current = controller.disc_status
            changed, self.changed_disc = self.changed_disc, None
            if changed is None and current is not None and current.mode == DiscMode.READY:
                return False
            try:
                disc = self.read_toc(self.device)
                if not disc.tracks:
                    raise ValueError('audio TOC has no tracks')
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                return self._apply(controller, DiscStatus(
                    DiscMode.UNREADABLE, device=self.device,
                    error=f'audio TOC: {type(exc).__name__}: {exc}'))
            self.loaded_toc = disc
            self.start_cdtext(disc)
            controller.state = controller.state.disc_loaded(
                tuple(track.number for track in disc.tracks))
            return self._set_status(controller,
                                    DiscStatus.ready(self.device, len(disc.tracks)))

    def media_removed(self) -> bool:
        """True if the CD was taken out (tray open or no disc), checked passively.

        Used while the player plays or the drive sleeps, when the normal
        poll() must not run. Only the drive status is asked (no TOC, no
        audio); the tested drives answer it without spinning up. Skipped
        (False) while the reader holds the drive or the drive is not the
        verified one.
        """
        if self._disconnected or not self.path_exists(self.device):
            return False
        if self.identity is not None and self.identity_reader(self.device) != self.identity:
            return False
        with self.lock(self.device, blocking=False) as acquired:
            if not acquired:
                return False
            try:
                code = self.media(self.device)
            except (OSError, ValueError):
                return False
        return code in (CDS_NO_DISC, CDS_TRAY_OPEN)

    def validate_before_play(self, controller) -> bool:
        """Revalidate physical disc identity at the explicit PLAY boundary.

        A changed, readable CD replaces the old track selection with track 1
        and allows the already explicit PLAY to continue. Missing/unreadable
        media or unverified USB identity blocks PLAY. No Sonos calls are made
        here; the serialized worker prevents concurrent monitor polling.
        """
        if not idle_for_disc_io(controller):
            return False
        current = controller.disc_status
        if (self._disconnected or self.identity is None or
                current is None or current.mode != DiscMode.READY or
                current.device != self.device or not self.path_exists(self.device)):
            self._mark_disconnected(controller) if not self.path_exists(self.device) else None
            return False
        if self.identity_reader(self.device) != self.identity:
            self._mark_disconnected(controller,
                                    'drive reconnect requires verified identity')
            return False
        with self.lock(self.device, blocking=False) as acquired:
            if not acquired:
                return False
            try:
                if self.media(self.device) != CDS_DISC_OK:
                    self._apply(controller, DiscStatus(DiscMode.LOADING, self.device))
                    return False
                disc = self.read_toc(self.device)
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                self._apply(controller, DiscStatus(
                    DiscMode.UNREADABLE, self.device,
                    error=f'PLAY revalidation: {type(exc).__name__}: {exc}'))
                return False
            if not disc.tracks:
                self._apply(controller, DiscStatus(
                    DiscMode.UNREADABLE, self.device,
                    error='PLAY revalidation: empty TOC'))
                return False
            self.changed_disc = None
            if self.loaded_toc is None or disc != self.loaded_toc:
                # Play the newly inserted disc only with its freshly validated
                # TOC and a reset selection (track 1), never the old selection.
                # The user's explicit PLAY remains the sole playback trigger.
                self.loaded_toc = disc
                # PLAY waits for nothing extra: no CD-TEXT on this path.
                self.loaded_cdtext = None
                controller.state = controller.state.disc_loaded(
                    tuple(t.number for t in disc.tracks))
                self._set_status(controller,
                                 DiscStatus.ready(self.device, len(disc.tracks)))
                return True
            return True

    def start_cdtext(self, disc):
        """Read CD-TEXT of ``disc`` in the background; READY does not wait for it."""
        self.cancel_cdtext(result=None)
        self.loaded_cdtext = None
        if self.read_cdtext is None:
            return
        if _toc_key(disc) in self._cdtext_hangs:
            self.loaded_cdtext = ('failed', {}, {}, 'cd-info timed out before on this disc; skipped')
            return
        self._cdtext_cancel = threading.Event()
        self.cdtext_pending = True
        self._cdtext_thread = threading.Thread(target=self._cdtext_job, args=(disc, self._cdtext_cancel),
                                               name='cdtext', daemon=True)
        self._cdtext_thread.start()

    def cancel_cdtext(self, *, result=('failed', {}, {}, 'skipped: PLAY or EJECT came first')):
        """Stop a running CD-TEXT read (PLAY or EJECT needs the drive now)."""
        thread = self._cdtext_thread
        if thread is None or not thread.is_alive():
            return
        self._cdtext_cancel.set()
        proc = self._cdtext_proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        thread.join(timeout=5)
        if result is not None:
            self.loaded_cdtext = result
        self.cdtext_pending = False
        print('[disc] CD-TEXT read cancelled', flush=True)

    def _cdtext_job(self, disc, cancel):
        value = None
        try:
            deadline = time.monotonic() + 5
            while not cancel.is_set():
                with self.lock(self.device, blocking=False) as acquired:
                    if acquired:
                        value = self._call_cdtext_reader(cancel)
                        break
                if time.monotonic() >= deadline:
                    break                   # drive stays busy: no CD-TEXT this time
                time.sleep(0.1)
        except Exception:
            value = None                    # CD-TEXT is optional; never blocks READY
        if cancel.is_set():
            return
        if isinstance(value, tuple) and value and value[-1] == 'cd-info timed out':
            self._cdtext_hangs.add(_toc_key(disc))
        self.loaded_cdtext = value
        self.cdtext_pending = False

    def _call_cdtext_reader(self, cancel):
        def run(args, **options):
            timeout = options.get('timeout')
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self._cdtext_proc = proc
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
            finally:
                self._cdtext_proc = None
            if cancel.is_set():
                raise OSError('cancelled')
            return subprocess.CompletedProcess(args, proc.returncode, out, err)
        try:
            return self.read_cdtext(self.device, runner=run)
        except TypeError:
            return self.read_cdtext(self.device)   # a reader without a runner (tests)

    def _recover_or_hold(self, controller):
        # Never guess a drive by /dev/srN or adopt a missing/duplicate serial.
        if self.identity is None:
            return self._mark_disconnected(
                controller, 'USB identity unavailable; manual restart required')
        try:
            device = self.recover(self.identity)
            if (not self.path_exists(device)
                    or self.identity_reader(device) != self.identity):
                raise RuntimeError('USB identity not verified after rediscovery')
            # Rebinding a power manager or reader path may fail; remain in
            # NO_DRIVE and retry later instead of publishing a false READY.
            if self.on_rebind is not None:
                self.on_rebind(device)
        except (OSError, RuntimeError, ValueError):
            return self._mark_disconnected(controller)
        self.rebind_verified(device)
        # A new media sample and TOC query follow in a future idle poll;
        # never reuse the old disc's tracks after USB reset.
        return self._set_status(controller, DiscStatus(DiscMode.LOADING, device))

    def rebind_verified(self, device: str):
        """Only call after exact USB identity validation and safe idle checks."""
        self.device = device
        self._disconnected = False
        self._disconnect_error = None

    def _mark_disconnected(self, controller, reason=None):
        if not self._disconnected:
            self._disconnect_error = reason
        self._disconnected = True
        return self._apply(controller, DiscStatus(
            DiscMode.NO_DRIVE, error=self._disconnect_error))

    @staticmethod
    def _set_status(controller, status):
        if controller.disc_status == status:
            return False
        controller.disc_status = status
        return True

    def _apply(self, controller, status):
        # Never leave an old track selection after removal or unknown media.
        self.loaded_toc = None
        self.loaded_cdtext = None
        if controller.state.track_numbers:
            controller.state = controller.state.disc_removed()
        return self._set_status(controller, status)
