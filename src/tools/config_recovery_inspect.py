"""OPS-01 independent read-only inspection of a persisted sandbox transaction.

No paths, writes, cleanup, service access, or restore operations. The caller
must supply an already-open directory descriptor; test subprocesses inherit
only the descriptor for their parent-created disposable sandbox.
This inspector is diagnostic evidence, NOT authorization for production apply.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat

from .config_candidate import build_candidate


_MAX_BYTES = 1024 * 1024
_EXPECTED_KEYS = frozenset({
    "version", "original_sha256", "candidate_sha256", "template_sha256",
    "selected_keys",
})


class RecoveryInspectionError(ValueError):
    """Insufficient evidence to classify an interrupted transaction."""


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _read_regular(dirfd: int, filename: str) -> bytes:
    """Reject symlinks/hardlinks/nonregular files without following links."""
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NONBLOCK |
                     getattr(os, "O_NOFOLLOW", 0), dir_fd=dirfd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_size > _MAX_BYTES or (info.st_mode & 0o077)):
                raise RecoveryInspectionError("unsafe transaction artifact")
            chunks = bytearray()
            while len(chunks) <= _MAX_BYTES:
                block = os.read(fd, min(65536, _MAX_BYTES + 1 - len(chunks)))
                if not block:
                    break
                chunks.extend(block)
            if len(chunks) > _MAX_BYTES:
                raise RecoveryInspectionError("oversized transaction artifact")
            return bytes(chunks)
        finally:
            os.close(fd)
    except OSError as exc:
        raise RecoveryInspectionError("transaction artifact unavailable") from exc


def inspect_persisted_transaction(dirfd: int, template: bytes) -> str:
    """Return 'not_replaced' or 'replacement_verified', without mutations.

    Only accepts an existing directory FD and a byte-for-byte matching
    template. No filesystem path is accepted. Corrupt, stale, changed or
    unrecognized states are rejected.
    """
    if not isinstance(dirfd, int) or dirfd < 0:
        raise RecoveryInspectionError("invalid directory descriptor")
    try:
        info = os.fstat(dirfd)
    except OSError as exc:
        raise RecoveryInspectionError("invalid directory descriptor") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise RecoveryInspectionError("directory descriptor required")
    if not isinstance(template, bytes):
        raise RecoveryInspectionError("reference bytes required")
    try:
        journal = json.loads(_read_regular(dirfd, "transaction-intent.json"))
        if not isinstance(journal, dict) or set(journal) != _EXPECTED_KEYS:
            raise RecoveryInspectionError("invalid transaction journal")
        if type(journal["version"]) is not int or journal["version"] != 1:
            raise RecoveryInspectionError("unsupported journal version")
        for field in ("original_sha256", "candidate_sha256", "template_sha256"):
            value = journal[field]
            if (not isinstance(value, str) or len(value) != 64 or
                    any(c not in "0123456789abcdef" for c in value)):
                raise RecoveryInspectionError("invalid journal checksum")
        selected = journal["selected_keys"]
        if (not isinstance(selected, list) or not selected or
                any(not isinstance(k, str) for k in selected) or
                len(set(selected)) != len(selected)):
            raise RecoveryInspectionError("invalid journal selection")
        if _hash(template) != journal["template_sha256"]:
            raise RecoveryInspectionError("reference template mismatch")
        original = _read_regular(dirfd, "backup-original.toml")
        if _hash(original) != journal["original_sha256"]:
            raise RecoveryInspectionError("backup checksum mismatch")
        candidate, original_hash = build_candidate(
            original, template, tuple(selected))
        if (original_hash != journal["original_sha256"] or
                _hash(candidate) != journal["candidate_sha256"]):
            raise RecoveryInspectionError("candidate provenance mismatch")
        current = _read_regular(dirfd, "current.toml")
        if current == original:
            return "not_replaced"
        if current == candidate:
            return "replacement_verified"
        raise RecoveryInspectionError("unrecognized current configuration")
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise RecoveryInspectionError("invalid transaction evidence") from exc
