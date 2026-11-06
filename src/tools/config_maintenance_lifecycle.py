"""Offline OPS-01 maintenance lifecycle and controlled pre-change abort.

This module is not wired into player startup, CLI, socket or systemd. It
coordinates only a caller-supplied offline barrier and guarded single-worker
executor. It does not perform or authorize a configuration change.
"""
from __future__ import annotations

from enum import Enum
import threading

from .config_guarded_worker import GuardedWorkerExecutor
from .config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)
from .config_maintenance_coordinator import drain_and_sample_offline


class LifecycleState(str, Enum):
    NORMAL = "NORMAL"
    PREPARING = "PREPARING"
    QUIESCED = "QUIESCED"
    CHANGE_POSSIBLE = "CHANGE_POSSIBLE"
    HALTED = "HALTED"


class OfflineMaintenanceLifecycle:
    """Single-use offline simulation with narrow, explicit resume semantics.

    The lifecycle owns its barrier. A clean, completed idle fence may be
    cancelled before any possible external modification, reopening admission
    on the same worker. Once a change may have started, or if the fence fails,
    the lifecycle will not reopen; separate recovery/revalidation is required.
    """

    def __init__(self, barrier: OfflineMaintenanceBarrier,
                 executor: GuardedWorkerExecutor):
        if (type(barrier) is not OfflineMaintenanceBarrier or
                type(executor) is not GuardedWorkerExecutor):
            raise TypeError("offline barrier and guarded worker required")
        if barrier.state != BarrierState.OPEN or barrier.in_flight:
            raise AdmissionRefused("fresh, idle offline barrier required")
        self._barrier = barrier
        self._executor = executor
        self._guard = threading.Lock()
        self._state = LifecycleState.NORMAL

    @property
    def state(self):
        with self._guard:
            return self._state

    def prepare(self, controller, *, timeout=5.0):
        """Drain/snapshot on one worker; return only a non-authorizing report.

        PREPARING is visible before any blocking wait. Concurrent calls to
        cancel, prepare or mark a change are refused until preparation ends.
        """
        with self._guard:
            if self._state != LifecycleState.NORMAL:
                raise AdmissionRefused("offline lifecycle already used")
            self._state = LifecycleState.PREPARING
        try:
            drain_and_sample_offline(
                self._barrier, self._executor, controller, timeout=timeout)
        except BaseException:
            # The coordinator closes the barrier after a failed drain or
            # uncertain fence; never reopen an unproven state.
            self._barrier.close()
            with self._guard:
                self._state = LifecycleState.HALTED
            raise
        with self._guard:
            self._state = LifecycleState.QUIESCED
        return self.report()

    def cancel_before_change(self):
        """Return to OPEN only after a successful fence and no change intent."""
        with self._guard:
            if self._state != LifecycleState.QUIESCED:
                raise AdmissionRefused("safe pre-change cancellation unavailable")
            self._barrier.reopen_before_change(self._executor)
            self._state = LifecycleState.NORMAL
        return self.report()

    def record_change_possible(self):
        """Model crossing the point at which a write *may* have been attempted.

        This records a simulation fact, not authorization to execute a write.
        Once called, resume is prohibited, even if subsequent work reports
        an exception or the files appear unchanged.
        """
        with self._guard:
            if self._state != LifecycleState.QUIESCED:
                raise AdmissionRefused("idle fence required before change intent")
            self._state = LifecycleState.CHANGE_POSSIBLE
        return self.report()

    def finish_after_external_review(self):
        """End the offline session; never reopen after a possible write.

        A real implementation must separately verify config, backup, service
        startup and writer exclusion. This method performs none of them.
        """
        with self._guard:
            if self._state != LifecycleState.CHANGE_POSSIBLE:
                raise AdmissionRefused("no change to finalize")
            self._barrier.close()
            self._state = LifecycleState.HALTED
        return self.report()

    def report(self):
        with self._guard:
            current = self._state
        return {
            "lifecycle_state": current.value,
            "barrier_state": self._barrier.state.value,
            "safe_abort_available": current == LifecycleState.QUIESCED,
            "recovery_review_required": current in (
                LifecycleState.CHANGE_POSSIBLE, LifecycleState.HALTED),
            "write_authorized": False,
            "production_maintenance_authorized": False,
            "service_restart_authorized": False,
        }
