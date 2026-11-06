"""Read-only identity and content consistency sampling for OPS-01.

Detect changed paths, in-place edits and atomic replacements between two
immediate observations. A stable sample is NOT writer exclusion, a lease, or
permission to update the production config. No service/drive/socket access.
"""
from __future__ import annotations

import hashlib
import os
import stat

_MAX_BYTES = 1024 * 1024


def _observe(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > _MAX_BYTES):
            raise ValueError("unsafe configuration")
        blocks = bytearray()
        while len(blocks) <= _MAX_BYTES:
            chunk = os.read(fd, min(65536, _MAX_BYTES + 1 - len(blocks)))
            if not chunk:
                break
            blocks.extend(chunk)
        if len(blocks) > _MAX_BYTES:
            raise ValueError("oversized configuration")
        after = os.fstat(fd)
        # Compare both metadata and bytes; metadata alone misses content
        # changes that retain size and restored timestamps.
        fingerprint = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
            after.st_ctime_ns, hashlib.sha256(blocks).digest(),
        )
        if ((before.st_dev, before.st_ino, before.st_size,
             before.st_mtime_ns, before.st_ctime_ns) != fingerprint[:5]):
            raise ValueError("configuration changed during read")
        path_stat = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1
                or (path_stat.st_dev, path_stat.st_ino) != fingerprint[:2]):
            raise ValueError("configuration pathname changed")
        return fingerprint
    finally:
        os.close(fd)


def sample_config_identity(path, *, observer=_observe):
    """Return only non-sensitive booleans; never disclose path or file bytes."""
    try:
        first = observer(path)
        second = observer(path)
        stable = first == second
    except (OSError, ValueError, TypeError):
        stable = False
    return {
        "read_only": True,
        "two_observations_consistent": stable,
        "writer_exclusion_proven": False,
        "write_authorized": False,
    }
