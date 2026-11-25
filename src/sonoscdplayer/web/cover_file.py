"""Bounded, descriptor-based serving of the current player's cover only.

No cover path is accepted from HTTP clients. A filename reported by the
player must be a direct child of the configured private cache directory.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from sonoscdplayer.metadata.cover_store import DEFAULT_COVER_DIR

MAX_SERVED_COVER_BYTES = 6 * 1024 * 1024


def read_player_cover(cover, directory=None):
    """Read one regular cover through O_NOFOLLOW descriptors; fail closed.

    Prevents path traversal, directory/leaf symlinks and files growing beyond
    the limit between stat and read. Returns (bytes, MIME type).
    """
    if type(cover) is not str or not cover or '\x00' in cover:
        raise ValueError('cover unavailable')
    root = Path(DEFAULT_COVER_DIR if directory is None else directory).absolute()
    candidate = Path(cover)
    if (not candidate.is_absolute() or '..' in candidate.parts or
            candidate.parent != root or
            candidate.suffix.lower() not in ('.jpg', '.jpeg', '.png')):
        raise ValueError('cover path outside store')
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        file_fd = os.open(candidate.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                          dir_fd=directory_fd)
        # fstat before fdopen: opening a directory for binary reading raises
        # IsADirectoryError on Linux before our regular-file validation runs.
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SERVED_COVER_BYTES:
                raise ValueError('invalid cover file')
            with os.fdopen(file_fd, 'rb') as source:
                file_fd = -1  # fdopen owns and closes the descriptor now.
                payload = source.read(MAX_SERVED_COVER_BYTES + 1)
            if not 0 < len(payload) <= MAX_SERVED_COVER_BYTES:
                raise ValueError('invalid cover size')
        finally:
            if file_fd >= 0:
                os.close(file_fd)
    finally:
        os.close(directory_fd)
    return payload, ('image/png' if candidate.suffix.lower() == '.png' else 'image/jpeg')
