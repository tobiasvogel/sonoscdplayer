"""Synthetic serialized-worker idle fence for OPS-01.

Caller-side coordination ONLY; not called by the live socket service.
No systemd, Sonos, optical drive, writer or configuration access.
FROZEN never authorizes a write or a production service stop.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import replace
import math
import time

from .config_controller_idle import sample_controller_idle
from .config_guarded_worker import GuardedWorkerExecutor
from .config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)


def freeze_after_worker_drain(barrier, executor, controller, *, timeout=5.0):
    """Take an offline idle sample after all admitted FIFO worker jobs.

    Caller MUST be outside this executor. The executor MUST be the same
    single-worker pool used for all barrier.submit operations. This guard
    checks its shape, not that no one bypassed the barrier or that systemd
    and outside processes are quiescent. No production authorization.
    """
    if type(barrier) is not OfflineMaintenanceBarrier:
        raise TypeError("offline barrier required")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("bounded timeout required")
    # ThreadPoolExecutor's implementation is FIFO with a single worker.
    # Reject multi-worker and arbitrary custom executors, which cannot
    # guarantee that this sample runs after previous controller mutations.
    if (type(executor) not in (ThreadPoolExecutor, GuardedWorkerExecutor)
            or executor._max_workers != 1):
        raise AdmissionRefused("single-worker FIFO executor required")

    # Check executor identity before changing admission state. The same lock
    # coordinates binding, tracked submissions and the drain transition.
    barrier.bind_executor(executor)
    deadline = time.monotonic() + timeout
    barrier.begin_draining()
    # Admission and executor submission are synchronized by barrier.submit.
    # Nothing admitted through that path can be queued behind this sample.
    try:
        if type(executor) is GuardedWorkerExecutor:
            future = executor._submit_idle_fence(sample_controller_idle, controller)
        else:
            future = executor.submit(sample_controller_idle, controller)
    except RuntimeError as exc:
        raise AdmissionRefused("idle sample could not be submitted") from exc
    try:
        sample = future.result(timeout=max(0, deadline - time.monotonic()))
    except FutureTimeout as exc:
        future.cancel()  # best effort: does not interrupt a running sample
        raise AdmissionRefused("worker idle sample timed out") from exc
    except Exception as exc:
        raise AdmissionRefused("worker idle sample failed") from exc

    # Futures can finish before their registered admission callbacks have
    # released the tickets. Never turn an expired deadline into a tiny new
    # waiting window: even a fully idle sample cannot authorize a late freeze.
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AdmissionRefused("worker idle deadline expired")
    if not barrier.wait_drained(remaining):
        raise AdmissionRefused("worker tasks remain pending")
    if time.monotonic() >= deadline:
        raise AdmissionRefused("worker idle deadline expired")
    if barrier.state != BarrierState.DRAINING:
        raise AdmissionRefused("barrier no longer draining")

    # The direct controller sample cannot independently prove the queue is
    # clear. We attest ONLY to this barrier's tracked submissions; external
    # activity, direct executor.submit and writer races remain unverified.
    proof = replace(sample, no_pending_external_activity=True)
    barrier.freeze(proof)
    return barrier.report()
