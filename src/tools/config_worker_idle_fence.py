"""Synthetic serialized-worker idle fence for OPS-01.

Caller-side coordination ONLY, not called by the live socket service. No
systemd, Sonos, optical drive, writer or configuration access. A FROZEN
result never authorizes a write or a production service stop.
"""
from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
import time

from .config_controller_idle import sample_controller_idle
from .config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)


def freeze_after_worker_drain(barrier, executor, controller, *, timeout=5.0):
    """Run the idle sample AFTER all previously admitted FIFO worker jobs.

    Must be called from outside a single-worker FIFO executor. All writer
    and controller submitters must use the same barrier.submit path.
    The fence is private local orchestration, never a socket request.
    An exception leaves the barrier DRAINING (or already CLOSED).
    """
    if type(barrier) is not OfflineMaintenanceBarrier:
        raise TypeError("offline barrier required")
    if type(timeout) not in (int, float) or not 0 < timeout <= 60:
        raise ValueError("bounded timeout required")
    deadline = time.monotonic() + timeout
    barrier.begin_draining()

    # No admitted task can be placed behind this probe: ticket issuance
    # and FIFO submission are atomic with begin_draining().
    future = executor.submit(sample_controller_idle, controller)
    try:
        sample = future.result(timeout=max(0, deadline - time.monotonic()))
    except FutureTimeout as exc:
        raise AdmissionRefused("worker idle sample timed out") from exc

    # result() may race the release callbacks of earlier Futures. Wait
    # for ALL accounted submissions to finish, without changing the state.
    if not barrier.wait_drained(max(0.000001, deadline - time.monotonic())):
        raise AdmissionRefused("worker tasks remain pending")
    if barrier.state != BarrierState.DRAINING:
        raise AdmissionRefused("barrier no longer draining")

    # Only the barrier proves that its OWN tracked queue has drained.
    # Systemd, external writers and untracked tasks remain unverified.
    proof = replace(sample, no_pending_external_activity=True)
    barrier.freeze(proof)
    return barrier.report()
