"""Cross-process advisory coordination of SonosCDPlayer drive commands.

One lock per device, in the invoking user's private runtime directory.
Not a global lock against third-party processes; all our reader generations and
motor commands participate. Lock files never contain PCM data.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import tempfile


def lock_path(device):
    if not re.fullmatch(r'/dev/sr[0-9]+', device):
        raise ValueError('invalid optical drive device')
    root = Path(os.environ.get('XDG_RUNTIME_DIR') or (Path(tempfile.gettempdir()) / f'sonoscdplayer-{os.getuid()}'))
    if not root.exists():
        root.mkdir(mode=0o700)
    s = root.lstat()
    if not root.is_dir() or s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise PermissionError('drive lock directory must be private and owned by current user')
    return root / ('sonoscdplayer-drive-' + device.rsplit('/', 1)[1] + '.lock')


@contextmanager
def drive_lock(device, *, blocking=True):
    path = lock_path(device)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        s = os.fstat(fd)
        if s.st_uid != os.getuid() or s.st_mode & 0o077:
            raise PermissionError('drive lock file is not private')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
