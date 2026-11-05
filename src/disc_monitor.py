"""M0.19b: conservative idle-only disc-state polling.

No Sonos commands. Never query a drive while a reader, stream, session or
unresolved group is attached. The bound /dev/srN must not be replaced by an
unverified USB device; only a unique matching USB identity may rebind.
"""
from __future__ import annotations

from pathlib import Path

from disc_startup import (drive_media_status, CDS_NO_DISC, CDS_TRAY_OPEN,
                          CDS_DRIVE_NOT_READY, CDS_DISC_OK)
from disc_state import DiscMode, DiscStatus
from drive_access import drive_lock
from drive_identity import usb_identity, recovered_device
from player_state import Mode


def idle_for_disc_io(controller) -> bool:
    return (controller.state.mode in (Mode.NO_DISC, Mode.STOPPED, Mode.ERROR)
            and not controller.owned_url
            and controller.player is None
            and not controller.server.alive()
            and not controller.server.reader_active()
            and not controller._joined
            and not controller._group_uncertain)


class DiscMonitor:
    """Only the serialized service worker calls poll()."""

    def __init__(self, device, identity, *, media=drive_media_status,
                 toc=None, path_exists=None, identity_reader=usb_identity,
                 lock=drive_lock, recover=recovered_device, on_rebind=None):
        from cd_toc import read_toc
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

            # No repeated cdparanoia scans for an already verified disc.
            current = controller.disc_status
            if current is not None and current.mode == DiscMode.READY:
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
            controller.state = controller.state.disc_loaded(
                tuple(track.number for track in disc.tracks))
            return self._set_status(controller,
                                    DiscStatus.ready(self.device, len(disc.tracks)))

    def validate_before_play(self, controller) -> bool:
        """Revalidate physical disc identity at the explicit PLAY boundary.

        A changed/absent CD invalidates stale selection and rejects this PLAY.
        The caller may retry only after observing the new track list.
        No Sonos calls are made. The single serialized worker prevents an
        overlapping monitor poll while this method is running.
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
            if self.loaded_toc is None or disc != self.loaded_toc:
                # Do not play the newly inserted disc on an old selection,
                # even when the two discs have the same track count.
                self.loaded_toc = disc
                controller.state = controller.state.disc_loaded(
                    tuple(t.number for t in disc.tracks))
                self._set_status(controller,
                                 DiscStatus.ready(self.device, len(disc.tracks)))
                return False
            return True

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
        if controller.state.track_numbers:
            controller.state = controller.state.disc_removed()
        return self._set_status(controller, status)
