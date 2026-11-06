"""OPS-01 explicit, one-shot in-process maintenance admission rehearsal.

Test/integration callers must construct and invoke this object themselves.
It is not instantiated by player_service.run(), registered on the Unix
control socket, exposed by CLI, or triggered by startup/systemd. It does
not establish exclusion of other writers, modify config or authorize apply.
"""
from __future__ import annotations

import threading

from .config_live_admission import InternalMaintenanceControl
from .config_maintenance_barrier import AdmissionRefused


class OneShotMaintenanceProbe:
    """Use the live loop's internal fence and reopen only after clean success.

    Call run_once from an external in-process coordinator thread, never from
    the controller's serial worker; this synchronous method waits for that
    worker to finish. Single use even if the probe fails or times out.
    """

    def __init__(self, control):
        if type(control) is not InternalMaintenanceControl:
            raise TypeError("explicit internal maintenance control required")
        self._control = control
        self._lock = threading.Lock()
        self._used = False

    def run_once(self, *, timeout=5.0):
        with self._lock:
            if self._used:
                raise AdmissionRefused("maintenance probe already consumed")
            self._used = True

        # If preparation fails, the underlying lifecycle fails closed. Do
        # not cancel or silently reopen an unverified/possibly busy worker.
        prepared = self._control.prepare(timeout=timeout)
        if (prepared.get("barrier_state") != "FROZEN"
                or prepared.get("lifecycle_state") != "QUIESCED"):
            raise AdmissionRefused("maintenance probe did not reach idle fence")
        # This one-shot diagnostic never crosses record_change_possible().
        # Only a successful no-write fence permits this controlled reopen.
        resumed = self._control.cancel_before_change()
        if (resumed.get("barrier_state") != "OPEN"
                or resumed.get("lifecycle_state") != "NORMAL"):
            raise AdmissionRefused("maintenance probe did not resume cleanly")
        return {
            "result": "idle_fence_and_clean_abort_observed",
            "single_use": True,
            "read_only": True,
            "writer_exclusion_verified": False,
            "production_maintenance_authorized": False,
            "write_authorized": False,
            "stop_authorized": False,
            "restart_authorized": False,
        }

    def report(self):
        with self._lock:
            consumed = self._used
        return {
            "consumed": consumed,
            "read_only": True,
            "production_maintenance_authorized": False,
            "write_authorized": False,
        }
