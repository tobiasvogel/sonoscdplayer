"""OPS-01 offline worker-drain and controller idle-fence coordination.

Not connected to player_service or any maintenance socket command. Use only
with the offline guarded single-worker executor and synthetic controllers.
FROZEN is a simulation result, never permission for real config writes.
"""
from __future__ import annotations

from dataclasses import replace

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
    if not isinstance(timeout, (float, int)) or type(timeout) is bool or not 0 < timeout <= 60:
        raise ValueError("bounded positive timeout required")
    barrier.bind_executor(executor)
    barrier.begin_draining()
    try:
        if not barrier.wait_drained(timeout):
            raise AdmissionRefused("tracked work did not drain")
        def sample():
            # A successful tracked drain permits the worker to assert there
            # are no more admitted jobs in this offline model. Real player
            # integration and untracked external work remain unverified.
            proof = sample_controller_idle(controller)
            return replace(proof, no_pending_external_activity=True)
        proof = executor._submit_idle_fence(sample).result(timeout=timeout)
        barrier.freeze(proof)
    except BaseException:
        barrier.close()
        raise
    report = barrier.report()
    report["controller_idle_sampled_on_worker"] = True
    report["production_maintenance_authorized"] = False
    return report
