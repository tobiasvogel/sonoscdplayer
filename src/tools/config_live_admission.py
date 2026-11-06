"""OPS-01 internal-only maintenance admission control for a live service loop.

The object is injected into serve() by an in-process test/integration caller.
No CLI flag, network or socket endpoint, service auto-activation, config
writer, systemd command or production apply is provided. Running player
services do not instantiate this interface.
"""
from __future__ import annotations

import threading

from .config_guarded_worker import GuardedWorkerExecutor
from .config_maintenance_barrier import (
    AdmissionRefused, OfflineMaintenanceBarrier,
)
from .config_maintenance_lifecycle import OfflineMaintenanceLifecycle


class InternalMaintenanceControl:
    """Explicit in-process control; a single bound worker for each service."""

    def __init__(self, barrier: OfflineMaintenanceBarrier):
        if type(barrier) is not OfflineMaintenanceBarrier:
            raise TypeError("offline maintenance barrier required")
        self._barrier = barrier
        self._lock = threading.Lock()
        self._session = None
        self._controller = None
        self._retired = False

    def bind(self, executor, controller):
        """Called by serve once before its normal command/refresh loop."""
        if type(executor) is not GuardedWorkerExecutor:
            raise TypeError("guarded single-worker executor required")
        with self._lock:
            if self._retired:
                raise AdmissionRefused("maintenance control retired")
            if self._session is not None:
                raise AdmissionRefused("maintenance control already bound")
            self._barrier.bind_executor(executor)
            self._session = OfflineMaintenanceLifecycle(self._barrier, executor)
            self._controller = controller

    def prepare(self, *, timeout=5.0):
        """Block the caller while the service loop continues answering status.

        Invoke from a separate in-process coordinator thread, NEVER from the
        serialized player worker itself; this waits on that worker's fence.
        It does not permit writes or shutdown.
        """
        with self._lock:
            session = self._session
            controller = self._controller
        if session is None:
            raise AdmissionRefused("maintenance control not bound")
        prepared = session.prepare(controller, timeout=timeout)
        with self._lock:
            if self._retired:
                raise AdmissionRefused("maintenance control retired during preparation")
        return prepared

    def cancel_before_change(self):
        """Restore normal admission after a completed no-write idle fence."""
        with self._lock:
            if self._retired:
                raise AdmissionRefused("maintenance control retired")
            session = self._session
            if session is None:
                raise AdmissionRefused("maintenance control not bound")
            # Hold the retirement lock across reopen: a late abort cannot
            # reopen the barrier once retire() has returned.
            return session.cancel_before_change()

    def retire(self):
        """Irreversibly invalidate the service's control and admission.

        Safe even before bind, on an incomplete drain, or after a clean abort.
        Never wait for worker completion under this lock.
        """
        with self._lock:
            self._retired = True
            self._barrier.close()

    def report(self):
        with self._lock:
            session = self._session
            retired = self._retired
        if session is None:
            return {"attached": False, "retired": retired,
                    "write_authorized": False,
                    "production_maintenance_authorized": False}
        return {"attached": True, "retired": retired, **session.report()}
