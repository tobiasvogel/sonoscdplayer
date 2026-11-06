"""OPS-01 offline worker-drain and controller idle-fence coordination.

Not connected to player_service or any maintenance socket command. Use only
with the offline guarded single-worker executor and synthetic controllers.
FROZEN is a simulation result, never permission for real config writes.
"""
from __future__ import annotations

from dataclasses import replace
import math
import time
from concurrent.futures import TimeoutError as FutureTimeout

from .config_controller_idle import sample_controller_idle
from .config_guarded_worker import GuardedWorkerExecutor
from .config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)


def drain_and_sample_offline(barrier: OfflineMaintenanceBarrier,
                             executor: GuardedWorkerExecutor, controller,
                             *, timeout: float = 5.0) -> dict:
    """Close simulated admission, drain tracked work, then fence on same worker.

    The fence is queued only after tracked work has finished. New tracked
    submissions are rejected while DRAINING. The worker samples actual
    in-memory controller attributes, not the cached status publisher.
    No side effects except the offline barrier state transition.
    """
    if (type(barrier) is not OfflineMaintenanceBarrier or
            type(executor) is not GuardedWorkerExecutor):
        raise TypeError("offline barrier and guarded worker required")
    if (type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= 60):
        raise ValueError("finite bounded positive timeout required")
    barrier.bind_executor(executor)
    deadline = time.monotonic() + timeout
    barrier.begin_draining()
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not barrier.wait_drained(remaining):
            raise AdmissionRefused("tracked work did not drain")
        def sample():
            # A successful tracked drain permits the worker to assert there
            # are no more admitted jobs in this offline model. Real player
            # integration and untracked external work remain unverified.
            proof = sample_controller_idle(controller)
            return replace(proof, no_pending_external_activity=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AdmissionRefused("idle fence deadline expired")
        future = executor._submit_idle_fence(sample)
        try:
            proof = future.result(timeout=max(0, deadline - time.monotonic()))
        except FutureTimeout as exc:
            future.cancel()
            raise AdmissionRefused("idle fence timed out") from exc
        if time.monotonic() >= deadline:
            raise AdmissionRefused("idle fence deadline expired")
        barrier.freeze(proof)
    except BaseException:
        barrier.close()
        raise
    report = barrier.report()
    report["controller_idle_sampled_on_worker"] = True
    report["production_maintenance_authorized"] = False
    return report
