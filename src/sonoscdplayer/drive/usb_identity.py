"""Recognize the same USB drive again after it reconnected as another /dev/srN.

A drive is identified by USB vendor id, product id and serial number. Without
a serial number a reconnected drive is never adopted. Only sysfs is read.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class UsbDriveId:
    """USB vendor id, product id (four hex digits each) and serial number."""
    vendor: str
    product: str
    serial: str


def usb_identity(device: str, *, sys_root='/sys/class/block') -> UsbDriveId | None:
    """USB identity of ``/dev/srN``, or None if it is not USB or has no serial."""
    match = re.fullmatch(r'/dev/(sr[0-9]+)', device)
    if not match:
        return None
    path = Path(sys_root) / match.group(1) / 'device'
    if not path.exists():
        return None
    for node in (path.resolve(), *path.resolve().parents):
        try:
            vendor = (node / 'idVendor').read_text().strip().lower()
            product = (node / 'idProduct').read_text().strip().lower()
            serial = (node / 'serial').read_text().strip()
        except (OSError, UnicodeError):
            continue
        if (re.fullmatch(r'[0-9a-f]{4}', vendor)
                and re.fullmatch(r'[0-9a-f]{4}', product) and serial):
            return UsbDriveId(vendor, product, serial)
    return None


def recovered_device(original: UsbDriveId, *, sys_root='/sys/class/block',
                     dev_root='/dev', list_nodes=None) -> str:
    """Require exactly one currently connected device with the same USB ID."""
    if original is None:
        raise RuntimeError('USB drive identity was not available at startup')
    root = Path(sys_root)
    names = (list_nodes() if list_nodes is not None
             else (p.name for p in root.glob('sr[0-9]*')))
    matches = []
    for name in names:
        if not re.fullmatch(r'sr[0-9]+', name):
            continue
        candidate = str(Path(dev_root) / name)
        # Identity reader only accepts /dev paths; map sysroot separately.
        if (Path(dev_root) / name).exists() and usb_identity(
                '/dev/' + name, sys_root=sys_root) == original:
            matches.append(candidate)
    if len(matches) != 1:
        raise RuntimeError('original optical drive not uniquely rediscovered')
    return matches[0]
