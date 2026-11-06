"""OPS-01 disposable-sandbox durable transaction phase journal.

Only an already-open sandbox directory descriptor is accepted. Each transition
writes a private staged JSON file, fsyncs it, atomically replaces the phase
record and fsyncs the directory. Phases are diagnostic, never authorization.
The checksums and actual file bytes remain the recovery source of truth.
"""
from __future__ import annotations

import json
import os
import stat

_PHASE_FILE = "transaction-phase.json"
_STAGE_FILE = "transaction-phase-staged.json"
_ALLOWED = {
    None: {"BACKED_UP"},
    "BACKED_UP": {"PREPARED"},
    "PREPARED": {"REPLACED", "ROLLBACK_PREPARED"},
    "REPLACED": {"VERIFIED", "ROLLBACK_PREPARED"},
    "VERIFIED": {"ROLLBACK_PREPARED"},
    "ROLLBACK_PREPARED": {"ROLLED_BACK"},
    "ROLLED_BACK": set(),
}


class PhaseRejected(ValueError):
    """Missing, unsafe or inconsistent synthetic transaction phase."""


def read_phase(dirfd):
    """Read a strictly formatted private phase record or None if absent."""
    try:
        fd = os.open(_PHASE_FILE, os.O_RDONLY | os.O_NONBLOCK |
                     getattr(os, "O_NOFOLLOW", 0), dir_fd=dirfd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PhaseRejected("phase record unavailable") from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > 512 or info.st_mode & 0o077):
            raise PhaseRejected("unsafe phase record")
        raw = os.read(fd, 513)
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise PhaseRejected("invalid phase record") from exc
        if (type(record) is not dict or set(record) != {"version", "phase"}
                or type(record["version"]) is not int
                or record["version"] != 1
                or type(record["phase"]) is not str
                or record["phase"] not in _ALLOWED or
                record["phase"] is None):
            raise PhaseRejected("invalid phase record")
        return record["phase"]
    finally:
        os.close(fd)


def persist_phase(dirfd, phase):
    """Advance one durable sandbox phase; never overwrite an unknown stage."""
    if type(phase) is not str or phase not in _ALLOWED or phase is None:
        raise PhaseRejected("invalid phase transition")
    current = read_phase(dirfd)
    if phase not in _ALLOWED[current]:
        raise PhaseRejected("invalid phase transition")
    data = json.dumps({"version": 1, "phase": phase},
                      sort_keys=True, separators=(",", ":")).encode("ascii")
    fd = os.open(_STAGE_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dirfd)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("incomplete phase journal write")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        os.unlink(_STAGE_FILE, dir_fd=dirfd)
        raise
    else:
        os.close(fd)
    os.replace(_STAGE_FILE, _PHASE_FILE,
               src_dir_fd=dirfd, dst_dir_fd=dirfd)
    os.fsync(dirfd)
