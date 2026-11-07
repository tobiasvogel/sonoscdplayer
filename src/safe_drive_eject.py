"""Conservative single-drive tray eject, serialized by the player-service worker.

No Sonos STOP, no force, no implicit USB rebind. Never actuate during a
reader/stream, ambiguous ownership, unsettled group, or unverified drive.
"""
from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

from disc_startup import CDS_DISC_OK, CDS_TRAY_OPEN, drive_media_status
from disc_state import DiscMode, DiscStatus
from drive_access import drive_lock
from drive_identity import usb_identity
from player_state import Mode

# Linux include/uapi/linux/cdrom.h
CDROMEJECT = 0x5309
CDROM_LOCKDOOR = 0x5329


def eject_tray(device):
    """Unlock then issue one CDROMEJECT, as in util-linux eject -r.

    The unlock must succeed before sending any eject command. No retry or
    automatic fallback is permitted; one operator action means one attempt.
    """
    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.ioctl(fd, CDROM_LOCKDOOR, 0)
        fcntl.ioctl(fd, CDROMEJECT)
    finally:
        os.close(fd)


def eject_verified_idle(controller, monitor, *, identity_reader=usb_identity,
                        lock=drive_lock, media=drive_media_status,
                        actuate=eject_tray, path_exists=None,
                        sleep=time.sleep):
    """Single attempt; every check is repeated inside the serialized worker.

    A successful ioctl is only command acceptance, NOT proof that the tray
    physically opened. Confirm TRAY_OPEN within a bounded observation window,
    without sending a second eject command. An unconfirmed outcome is rejected
    and retains the previous cached disc/track until the monitor observes it.
    """
    if monitor is None:
        raise RuntimeError('EJECT refused: drive monitor unavailable')
    if (controller.state.mode != Mode.STOPPED
            or controller.owned_url is not None
            or controller.player is not None
            or controller.server.alive()
            or controller.server.reader_active()
            or controller._joined
            or controller._group_uncertain):
        raise RuntimeError('EJECT refused: player is not fully idle')

    device = monitor.device
    disc = controller.disc_status
    if (monitor._disconnected or monitor.identity is None
            or disc is None or disc.mode != DiscMode.READY
            or disc.device != device or controller.server.device != device
            or monitor.loaded_toc is None):
        raise RuntimeError('EJECT refused: drive/disc state not verified')
    exists = path_exists if path_exists is not None else lambda p: Path(p).exists()
    if not exists(device) or identity_reader(device) != monitor.identity:
        raise RuntimeError('EJECT refused: drive identity mismatch')

    with lock(device, blocking=False) as acquired:
        if not acquired:
            raise RuntimeError('EJECT refused: optical drive busy')
        # Revalidate within the same device lock before touching the tray.
        if (controller.state.mode != Mode.STOPPED or controller.owned_url is not None
                or controller.player is not None or controller.server.alive()
                or controller.server.reader_active() or controller._joined
                or controller._group_uncertain
                or not exists(device) or identity_reader(device) != monitor.identity
                or media(device) != CDS_DISC_OK):
            raise RuntimeError('EJECT refused: idle/drive verification changed')
        actuate(device)
        # The command may return before the mechanism finishes moving.
        # Keep the drive lock while observing: our motor keepalive/monitor
        # cannot race the eject operation. No actuation is repeated.
        opened = False
        for attempt in range(5):
            if attempt:
                sleep(0.5)
            if media(device) == CDS_TRAY_OPEN:
                opened = True
                break
        if not opened:
            raise RuntimeError('EJECT unconfirmed: tray did not report open; not retrying')

    # Only after physical tray-open confirmation invalidate old CD selection.
    controller.state = controller.state.disc_removed()
    controller.display_toc = None
    monitor.loaded_toc = None
    controller.disc_status = DiscStatus(DiscMode.EJECTED, device)
    return True
