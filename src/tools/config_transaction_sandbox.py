"""OPS-01 isolated filesystem transaction rehearsal.

This is deliberately NOT a production apply interface. Every instance creates
its own TemporaryDirectory. No caller-provided target path, systemd access,
Sonos control, hardware access or service restart is possible through this API.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .config_candidate import build_candidate


class TransactionRejected(ValueError):
    """A transaction was refused without publishing a candidate."""


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class OfflineSandbox:
    """Exercise backup -> atomic replacement -> guarded restore on test files.

    A fresh sandbox owns all its paths. A synced, value-free intent journal
    supports offline restart simulations. This is NOT production crash recovery:
    service preflight, durable recovery locking and real crash testing are
    independently required before production support can be exposed.
    """

    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="ops01-offline-")
        self.directory = Path(self._temporary.name)
        self._dirfd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY |
                              getattr(os, "O_NOFOLLOW", 0))
        self._backup_name = None
        self._backup_digest = None
        self._current_digest = None
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            os.close(self._dirfd)
            self._temporary.cleanup()

    def _require_open(self):
        if self._closed:
            raise TransactionRejected("sandbox closed")

    def _read(self, name):
        self._require_open()
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK |
                         getattr(os, "O_NOFOLLOW", 0), dir_fd=self._dirfd)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > 1024 * 1024:
                    raise TransactionRejected("unsafe test file")
                with os.fdopen(os.dup(fd), "rb") as stream:
                    data = stream.read(1024 * 1024 + 1)
                if len(data) > 1024 * 1024:
                    raise TransactionRejected("oversized test file")
                return data, (st.st_dev, st.st_ino, st.st_mode)
            finally:
                os.close(fd)
        except OSError as exc:
            raise TransactionRejected("test file unavailable or unsafe") from exc

    def _create_synced(self, name, data):
        self._require_open()
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=self._dirfd)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(os.dup(fd), "wb") as stream:
                stream.write(data)
                stream.flush()
            os.fsync(fd)
        finally:
            os.close(fd)

    def _check_unchanged(self, name, before_data, identity):
        actual, actual_identity = self._read(name)
        if actual != before_data or actual_identity != identity:
            raise TransactionRejected("test file changed during transaction")

    def _journal(self):
        """Read the sandbox's persistent, redacted transaction intent."""
        try:
            data, _ = self._read("transaction-intent.json")
            item = json.loads(data)
            if (not isinstance(item, dict) or item.get("version") != 1 or
                    set(item) != {"version", "original_sha256",
                                  "candidate_sha256", "template_sha256",
                                  "selected_keys"}):
                raise TransactionRejected("invalid transaction intent")
            if (any(not isinstance(item[field], str) or
                    len(item[field]) != 64 or
                    any(c not in "0123456789abcdef" for c in item[field])
                    for field in ("original_sha256", "candidate_sha256",
                                  "template_sha256")) or
                    not isinstance(item["selected_keys"], list) or
                    not item["selected_keys"] or
                    any(not isinstance(k, str) for k in item["selected_keys"])):
                raise TransactionRejected("invalid transaction intent")
            return item
        except (ValueError, UnicodeError, TypeError) as exc:
            raise TransactionRejected("invalid transaction intent") from exc

    def simulate_process_restart(self):
        """Discard volatile bookkeeping, retaining only sandbox files.

        This does not kill a process or reopen its directory. It is an
        intentionally limited, deterministic restart simulation.
        """
        self._require_open()
        self._backup_name = None
        self._backup_digest = None
        self._current_digest = None

    def inspect_after_restart(self, template: bytes):
        """Read-only recovery classification using persisted intent.

        The operator must explicitly supply the *same* reference template;
        the journal alone cannot authorize a rollback or replacement.
        """
        intent = self._journal()
        if digest(template) != intent["template_sha256"]:
            raise TransactionRejected("reference template has changed")
        original, _ = self._read("backup-original.toml")
        if digest(original) != intent["original_sha256"]:
            raise TransactionRejected("backup does not match journal")
        candidate, source_sha = build_candidate(
            original, template, tuple(intent["selected_keys"]))
        if (source_sha != intent["original_sha256"] or
                digest(candidate) != intent["candidate_sha256"]):
            raise TransactionRejected("candidate does not match journal")
        current, _ = self._read("current.toml")
        if current == original:
            return "not_replaced"
        if current == candidate:
            return "replacement_verified"
        raise TransactionRejected("unrecognized current configuration")

    def arm_rollback_after_restart(self, template: bytes):
        """Re-arm rollback only on independently verified sandbox bytes."""
        if self._backup_name is not None:
            raise TransactionRejected("transaction already tracked")
        result = self.inspect_after_restart(template)
        if result != "replacement_verified":
            raise TransactionRejected("no verified replacement to restore")
        intent = self._journal()
        self._backup_name = "backup-original.toml"
        self._backup_digest = intent["original_sha256"]
        self._current_digest = intent["candidate_sha256"]
        return self._current_digest

    def seed(self, raw: bytes):
        """Create a synthetic original exactly once."""
        self._require_open()
        if self._current_digest is not None:
            raise TransactionRejected("already seeded")
        if not isinstance(raw, bytes):
            raise TransactionRejected("bytes required")
        # The candidate parser will validate TOML on apply.
        self._create_synced("current.toml", raw)
        os.fsync(self._dirfd)
        self._current_digest = digest(raw)
        return self._current_digest

    def read_current(self):
        return self._read("current.toml")[0]

    def apply(self, template: bytes, selected_keys: tuple[str, ...],
              *, expected_sha256: str, fail_at=None):
        """Rehearse safe apply; no real files are accessible.

        fail_at='before_replace' or 'after_replace' injects a failure.
        """
        self._require_open()
        if self._backup_name is not None or (self.directory / "transaction-intent.json").exists():
            raise TransactionRejected("transaction already applied or recovery required")
        original, identity = self._read("current.toml")
        if (expected_sha256 != digest(original) or
                expected_sha256 != self._current_digest):
            raise TransactionRejected("stale apply checksum")
        candidate, confirmed_sha = build_candidate(original, template, selected_keys)
        if confirmed_sha != expected_sha256:
            raise TransactionRejected("stale proposal")

        backup = "backup-original.toml"
        staged = "candidate-staged.toml"
        # No preexisting names allowed; never delete arbitrary preexisting files.
        if any((self.directory / n).exists() or (self.directory / n).is_symlink()
               for n in (backup, staged)):
            raise TransactionRejected("transaction artifact already exists")

        self._create_synced(backup, original)
        os.fsync(self._dirfd)
        try:
            self._create_synced(staged, candidate)
            # Revalidate staged bytes, backup integrity and original pathname.
            staged_data, _ = self._read(staged)
            backup_data, _ = self._read(backup)
            if staged_data != candidate or backup_data != original:
                raise TransactionRejected("transaction content mismatch")
            self._check_unchanged("current.toml", original, identity)
            journal = {
                "version": 1,
                "original_sha256": digest(original),
                "candidate_sha256": digest(candidate),
                "template_sha256": digest(template),
                "selected_keys": list(selected_keys),
            }
            self._create_synced("transaction-intent.json", (
                json.dumps(journal, sort_keys=True, separators=(",", ":"))
            ).encode("ascii"))
            os.fsync(self._dirfd)
            if fail_at == "before_replace":
                raise TransactionRejected("injected failure before replace")
            if fail_at not in (None, "after_replace"):
                raise TransactionRejected("unknown fault injection")
            os.replace(staged, "current.toml",
                       src_dir_fd=self._dirfd, dst_dir_fd=self._dirfd)
            os.fsync(self._dirfd)
            if fail_at == "after_replace":
                raise TransactionRejected("injected failure after replace")
        finally:
            # Only our own staged name is eligible for cleanup.
            try:
                os.unlink(staged, dir_fd=self._dirfd)
            except FileNotFoundError:
                pass
        self._backup_name = backup
        self._backup_digest = digest(original)
        self._current_digest = digest(candidate)
        return self._current_digest

    def recover_interrupted(self, template: bytes,
                            selected_keys: tuple[str, ...],
                            *, expected_original_sha256: str):
        """Recognize an interrupted *sandbox* apply, without changing files.

        Requires the original checksum from the explicit plan and recomputes
        the only acceptable candidate from the backed-up original. Unknown or
        tampered files fail closed. For a verified post-replace interruption,
        re-arm the existing explicit rollback operation. A pre-replace
        interruption needs no restoration and is left unchanged for inspection.
        """
        self._require_open()
        if self._backup_name is not None:
            raise TransactionRejected("transaction already tracked")
        intent = self._journal()
        if (intent["original_sha256"] != expected_original_sha256 or
                intent["template_sha256"] != digest(template) or
                tuple(intent["selected_keys"]) != selected_keys):
            raise TransactionRejected("transaction intent mismatch")
        original, _ = self._read("backup-original.toml")
        if digest(original) != expected_original_sha256:
            raise TransactionRejected("backup does not match original checksum")
        candidate, computed_sha = build_candidate(original, template, selected_keys)
        if computed_sha != expected_original_sha256:
            raise TransactionRejected("candidate provenance mismatch")
        current, _ = self._read("current.toml")
        if current == original:
            return "not_replaced"
        if (current != candidate or
                digest(candidate) != intent["candidate_sha256"]):
            raise TransactionRejected("interrupted target differs from known candidate")
        self._backup_name = "backup-original.toml"
        self._backup_digest = digest(original)
        self._current_digest = digest(candidate)
        return "replacement_verified"

    def rollback(self, *, expected_sha256: str):
        """Rehearse an explicit checksum-gated restoration, once."""
        self._require_open()
        if self._backup_name is None:
            raise TransactionRejected("no completed transaction")
        current, identity = self._read("current.toml")
        if (expected_sha256 != self._current_digest or
                digest(current) != self._current_digest):
            raise TransactionRejected("stale rollback checksum")
        original, _ = self._read(self._backup_name)
        if digest(original) != self._backup_digest:
            raise TransactionRejected("backup integrity mismatch")
        restored_name = "rollback-staged.toml"
        if (self.directory / restored_name).exists() or (self.directory / restored_name).is_symlink():
            raise TransactionRejected("restore artifact already exists")
        try:
            self._create_synced(restored_name, original)
            restored_data, _ = self._read(restored_name)
            if restored_data != original:
                raise TransactionRejected("restore content mismatch")
            self._check_unchanged("current.toml", current, identity)
            os.replace(restored_name, "current.toml",
                       src_dir_fd=self._dirfd, dst_dir_fd=self._dirfd)
            os.fsync(self._dirfd)
        finally:
            try:
                os.unlink(restored_name, dir_fd=self._dirfd)
            except FileNotFoundError:
                pass
        self._current_digest = digest(original)
        self._backup_name = None
        self._backup_digest = None
        return self._current_digest
