"""Strict USB optical-drive identity across /dev/srN renumbering.

Never adopt another USB drive when the serial cannot be verified.
No optical IO or motor commands are performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class UsbDriveId:
    vendor: str
    product: str
    serial: str


def usb_identity(device: str, *, sys_root='/sys/class/block') -> UsbDriveId | None:
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
