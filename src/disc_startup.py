"""M0.19a: one-time non-playing disc classification at service startup.

No polling, motor control, Sonos requests or re-reads during playback. Media
insertion/removal monitoring is deliberately deferred to M0.19b.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re

from disc_state import DiscMode, DiscStatus

# Linux cdrom.h: CDROM_DRIVE_STATUS, CDSL_CURRENT, CDS_*.
CDROM_DRIVE_STATUS = 0x5326
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4


def optical_devices(*, sys_root=Path('/sys/class/block'),
                    dev_root=Path('/dev')) -> tuple[str, ...]:
    """Enumerate device nodes, never adopt a new drive by assumption."""
    names = sorted((p.name for p in sys_root.glob('sr[0-9]*')
                    if re.fullmatch(r'sr[0-9]+', p.name)),
                   key=lambda name: int(name[2:]))
    return tuple(str(dev_root / name) for name in names if (dev_root / name).exists())


def drive_media_status(device: str) -> int:
    """One read-only ioctl; absence of reliable status is not 'no disc'."""
    # Nonblocking open avoids waiting for media on drives with an empty tray.
    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        return fcntl.ioctl(fd, CDROM_DRIVE_STATUS, 0)
    finally:
        os.close(fd)


def classify_unavailable(device: str | None, media_status=None,
                         *, error: str | None = None) -> DiscStatus:
    if device is None:
        return DiscStatus(DiscMode.NO_DRIVE, error=error)
    try:
        code = media_status(device) if media_status is not None else drive_media_status(device)
    except (OSError, ValueError):
        code = None
    modes = {CDS_NO_DISC: DiscMode.NO_DISC,
             CDS_TRAY_OPEN: DiscMode.EJECTED,
             CDS_DRIVE_NOT_READY: DiscMode.LOADING}
    mode = modes.get(code, DiscMode.UNREADABLE)
    # Do not label an absent disc "unreadable"; avoid stale probe errors.
    return DiscStatus(mode, device=device,
                      error=error if mode == DiscMode.UNREADABLE else None)


def inspect_initial_disc(base, *, requested_device='auto',
                         detect=None, read=None, candidates=None,
                         media_status=None):
    """Return (DiscStatus, DiscTOC | None). Query TOC only for detected CD-DA."""
    from cd_toc import read_toc
    from sonos_parallel_test import detect_drive
    detect = detect if detect is not None else detect_drive
    read = read if read is not None else read_toc
    paths = tuple(candidates) if candidates is not None else optical_devices()
    if requested_device != 'auto':
        paths = (requested_device,) if Path(requested_device).exists() else ()
    if not paths:
        return DiscStatus(DiscMode.NO_DRIVE), None

    try:
        device = detect(base) if requested_device == 'auto' else requested_device
        if device not in paths:
            raise RuntimeError('selected drive is not among available devices')
        disc = read(device)
        if not disc.tracks:
            raise ValueError('empty audio TOC')
        return DiscStatus.ready(device, len(disc.tracks)), disc
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        # Multiple candidate drives without exactly one readable CD are
        # ambiguous. Never silently select one for playback or future commands.
        candidate = paths[0] if len(paths) == 1 else None
        if candidate is None:
            return DiscStatus(DiscMode.UNREADABLE,
                              error='no unique readable audio-CD drive'), None
        return classify_unavailable(candidate, media_status,
                                    error=f'{type(exc).__name__}: {exc}'), None
