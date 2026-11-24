"""Bounded, read-only cover cache statistics for the local web client.

No image decoding, arbitrary user paths, deletion or player RPC.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
import stat

from sonoscdplayer.metadata.cover_store import DEFAULT_COVER_DIR

MAX_COVERS = 10000
# CoverStore uses SHA-256(release UUID); ManualCoverStore uses the TOC-qualified
# 32-hex disc token. Other JPEGs must not enter a future deletion inventory.
_AUTO_NAME = re.compile(r'[0-9a-f]{64}\.jpg\Z')
_MANUAL_NAME = re.compile(r'manual-[0-9a-f]{32}\.jpg\Z')


def cover_kind(name):
    """Identify only filenames written by the two supported cover stores."""
    if not isinstance(name, str):
        return None
    if _AUTO_NAME.fullmatch(name):
        return 'automatic'
    if _MANUAL_NAME.fullmatch(name):
        return 'manual'
    return None


def cover_storage_snapshot(budget_mb, directory=None):
    """Report recognized store files only; fail on I/O errors."""
    if type(budget_mb) is not int or not 1 <= budget_mb <= 10000:
        raise ValueError('invalid cover budget')
    root = Path(DEFAULT_COVER_DIR if directory is None else directory)
    count = manual = used = manual_bytes = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                kind = cover_kind(entry.name)
                if kind is None:
                    continue
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    continue
                count += 1
                if count > MAX_COVERS:
                    raise OSError('too many cover files')
                used += info.st_size
                if kind == 'manual':
                    manual += 1
                    manual_bytes += info.st_size
    except FileNotFoundError:
        # No cache directory has been created yet.
        pass
    return {'ok': True, 'cover_count': count, 'manual_count': manual,
            'automatic_count': count - manual, 'used_bytes': used,
            'manual_bytes': manual_bytes, 'automatic_bytes': used - manual_bytes,
            'budget_bytes': budget_mb * 1024 * 1024}
