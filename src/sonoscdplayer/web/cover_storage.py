"""Bounded, read-only cover cache statistics for the local web client.

No image decoding, arbitrary user paths, deletion or player RPC.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from sonoscdplayer.metadata.cover_store import DEFAULT_COVER_DIR

MAX_COVERS = 10000


def cover_storage_snapshot(budget_mb, directory=None):
    """Report regular JPEG files only; fail rather than claim zero on I/O errors."""
    if type(budget_mb) is not int or not 1 <= budget_mb <= 10000:
        raise ValueError('invalid cover budget')
    root = Path(DEFAULT_COVER_DIR if directory is None else directory)
    count = manual = used = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if not entry.name.endswith('.jpg'):
                    continue
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    continue
                count += 1
                if count > MAX_COVERS:
                    raise OSError('too many cover files')
                used += info.st_size
                manual += entry.name.startswith('manual-')
    except FileNotFoundError:
        # No cache directory has been created yet.
        pass
    return {'ok': True, 'cover_count': count, 'manual_count': manual,
            'used_bytes': used, 'budget_bytes': budget_mb * 1024 * 1024}
