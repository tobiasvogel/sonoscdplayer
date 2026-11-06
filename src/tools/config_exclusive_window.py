"""OPS-01 offline-only exclusive maintenance-window rehearsal.

Accepts an actual OfflineSandbox object, never a caller-selected production
path or directory descriptor. Reuses its existing cooperative writer lock,
the serialized idle fence and two identity observations. It does not write
configuration, run systemd, access Sonos or authorize production operations.
The sandbox lock file is the only artifact this coordinator may create.
"""
from __future__ import annotations

from .config_maintenance_barrier import AdmissionRefused, BarrierState
from .config_maintenance_lifecycle import (
    LifecycleState, OfflineMaintenanceLifecycle,
)
from .config_transaction_sandbox import OfflineSandbox
from .config_writer_lock import sandbox_writer_lock
from .config_writer_observation import _observe


class WindowRefused(ValueError):
    """The offline window could not establish consistent evidence."""


class OfflineExclusiveWindow:
    """Rehearse a maintenance window on one owned disposable sandbox.

    The initial fingerprint is captured before controller drain. After drain,
    acquire the cooperative lock and recheck the same sandbox file. Before
    recording any *possible* write, recheck under the held lock again.
    Lock and snapshots cannot exclude non-cooperating external writers.
    """

    def __init__(self, sandbox: OfflineSandbox,
                 lifecycle: OfflineMaintenanceLifecycle):
        if type(sandbox) is not OfflineSandbox or sandbox._closed:
            raise TypeError("open disposable sandbox required")
        if type(lifecycle) is not OfflineMaintenanceLifecycle:
            raise TypeError("offline maintenance lifecycle required")
        self._sandbox = sandbox
        self._lifecycle = lifecycle
        self._lock = None
        self._baseline = None
        self._started = False
        self._identity_last_checked = False

    def _current(self):
        self._sandbox._require_open()
        return self._sandbox.directory / "current.toml"

    def _check(self):
        self._identity_last_checked = False
        path = self._current()
        first = _observe(path)
        second = _observe(path)
        if first != second or first != self._baseline:
            raise WindowRefused("sandbox configuration changed")
        self._identity_last_checked = True
        return True

    def _unlock(self):
        lock, self._lock = self._lock, None
        self._identity_last_checked = False
        if lock is not None:
            lock.__exit__(None, None, None)

    def begin(self, controller, *, timeout=5.0):
        """Drain on the serialized worker, then take lock and check identity.

        A failed pre-change attempt releases the lock and, when a completed
        fence exists, cancels without any simulated configuration write.
        """
        if self._started or self._lifecycle.state != LifecycleState.NORMAL:
            raise WindowRefused("window already started")
        self._started = True
        identity_recheck_started = False
        try:
            self._baseline = _observe(self._current())
            self._lifecycle.prepare(controller, timeout=timeout)
            lock = sandbox_writer_lock(self._sandbox._dirfd)
            lock.__enter__()
            self._lock = lock
            identity_recheck_started = True
            self._check()
        except BaseException:
            self._unlock()
            if self._lifecycle.state == LifecycleState.QUIESCED:
                if identity_recheck_started:
                    self._lifecycle.halt_for_uncertain_evidence()
                else:
                    self._lifecycle.cancel_before_change()
            raise
        return self.report()

    def record_change_possible(self):
        """Recheck while holding the lock; never write or grant permission."""
        if (self._lock is None or
                self._lifecycle.state != LifecycleState.QUIESCED):
            raise WindowRefused("offline window not ready")
        try:
            self._check()
        except BaseException:
            # External edits bypassing the cooperative lock cannot be
            # classified as a clean no-write abort. Keep admission closed.
            self._lifecycle.halt_for_uncertain_evidence()
            self._unlock()
            raise
        self._lifecycle.record_change_possible()
        return self.report()

    def finish(self):
        """End window: clean abort may reopen, possible change stays CLOSED."""
        if self._lock is None:
            raise WindowRefused("no acquired offline window")
        try:
            state = self._lifecycle.state
            if state == LifecycleState.QUIESCED:
                try:
                    self._check()
                except BaseException:
                    self._lifecycle.halt_for_uncertain_evidence()
                    raise
                self._lifecycle.cancel_before_change()
            elif state == LifecycleState.CHANGE_POSSIBLE:
                self._lifecycle.finish_after_external_review()
            else:
                raise WindowRefused("unexpected offline lifecycle state")
        finally:
            self._unlock()
        return self.report()

    def report(self):
        lifecycle = self._lifecycle.report()
        return {
            **lifecycle,
            "sandbox_lock_held": self._lock is not None,
            "sandbox_identity_rechecked": self._identity_last_checked,
            "sandbox_identity_is_exclusive_proof": False,
            "external_writers_excluded": False,
            "write_authorized": False,
            "production_maintenance_authorized": False,
        }
