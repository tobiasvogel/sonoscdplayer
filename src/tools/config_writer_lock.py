"""OPS-01 cooperative advisory locking for *already-open* sandbox directories.

No filesystem path, service control or production migration is accepted.
Linux flock coordinates only cooperating writers that lock the same inode.
External writers bypassing this protocol remain a deployment blocker.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
import stat


class LockUnavailable(ValueError):
    """Refuse unsafe lock artifacts, or contention, without waiting."""


_LOCK_NAME = "ops01-writer.lock"


@contextmanager
def sandbox_writer_lock(dirfd: int):
    """Hold an exclusive nonblocking lock until the protected operation exits.

    Never unlink the stable lock file: removal can create separate inodes and
    defeat mutual exclusion. A lock is not a proof that third-party processes
    cannot change the target.
    """
    try:
        directory = os.fstat(dirfd)
        if not stat.S_ISDIR(directory.st_mode):
            raise LockUnavailable("directory descriptor required")
        fd = os.open(_LOCK_NAME, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC |
                     getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dirfd)
    except (OSError, TypeError, ValueError) as exc:
        raise LockUnavailable("writer lock unavailable") from exc
    try:
        try:
            info = os.fstat(fd)
            entry = os.stat(_LOCK_NAME, dir_fd=dirfd, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    (info.st_mode & 0o777) != 0o600 or
                    info.st_uid != os.geteuid() or
                    (info.st_dev, info.st_ino) != (entry.st_dev, entry.st_ino)):
                raise LockUnavailable("unsafe writer lock artifact")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Refuse replacement of the pathname after acquiring the lock.
            entry = os.stat(_LOCK_NAME, dir_fd=dirfd, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (entry.st_dev, entry.st_ino):
                raise LockUnavailable("writer lock identity changed")
        except (OSError, BlockingIOError) as exc:
            raise LockUnavailable("writer lock busy or unsafe") from exc
        yield
    finally:
        os.close(fd)
