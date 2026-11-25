"""Advisory lock for SonosCDPlayer cover writers and cache cleanup.

All cooperating processes must take this lock before mutating the cover
folder. Direct filesystem writes from unrelated processes are not protected.
Never acquire this lock on the serialized player control thread.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


@contextmanager
def locked_cover_directory(directory):
    """Hold an exclusive advisory lock on an existing, non-symlink directory."""
    fd = os.open(Path(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
