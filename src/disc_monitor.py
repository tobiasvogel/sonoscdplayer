"""M0.19b: conservative idle-only disc-state polling.

No Sonos commands. Never query a drive while a reader, stream, session or
unresolved group is attached. The bound /dev/srN must not be replaced by an
unverified USB device; /dev/srN recovery is deferred to M0.19c.
"""
from __future__ import annotations

from pathlib import Path

from disc_startup import (drive_media_status, CDS_NO_DISC, CDS_TRAY_OPEN,
                          CDS_DRIVE_NOT_READY, CDS_DISC_OK)
from disc_state import DiscMode, DiscStatus
from drive_access import drive_lock
from drive_identity import usb_identity
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
                 lock=drive_lock):
        from cd_toc import read_toc
        self.device = device
        self.identity = identity
        self.media = media
        self.read_toc = toc if toc is not None else read_toc
        self.path_exists = path_exists if path_exists is not None else lambda p: Path(p).exists()
        self.identity_reader = identity_reader
        self.lock = lock
        self._disconnected = False

    def poll(self, controller) -> bool:
        """Apply a verified idle observation; return whether status changed.

        A missing device clears the previous TOC but is never replaced by a
        different device at this phase. A drive busy under our shared lock
        retains its last observation and cannot start a concurrent query.
        """
        if not idle_for_disc_io(controller):
            return False

        if not self.path_exists(self.device):
            self._disconnected = True
            return self._apply(controller, DiscStatus(DiscMode.NO_DRIVE))

        current_id = self.identity_reader(self.device)
        if self._disconnected or (self.identity is not None and current_id != self.identity):
            # A disappearing/replaced USB node is not proof of original drive.
            self._disconnected = True
            return self._apply(controller, DiscStatus(
                DiscMode.NO_DRIVE, error='drive reconnect requires verified identity'))

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
            controller.state = controller.state.disc_loaded(
                tuple(track.number for track in disc.tracks))
            return self._set_status(controller,
                                    DiscStatus.ready(self.device, len(disc.tracks)))

    @staticmethod
    def _set_status(controller, status):
        if controller.disc_status == status:
            return False
        controller.disc_status = status
        return True

    def _apply(self, controller, status):
        # Never leave an old track selection after removal or unknown media.
        if controller.state.track_numbers:
            controller.state = controller.state.disc_removed()
        return self._set_status(controller, status)
