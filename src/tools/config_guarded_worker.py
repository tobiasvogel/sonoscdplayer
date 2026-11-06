"""OPS-01 guarded single-worker executor for optional offline admission.

Composition prevents ThreadPoolExecutor.submit(pool, ...) from bypassing the
ordinary submit() refusal. The underlying executor is deliberately not exposed
through the public interface. Python private attributes are not a security
boundary against hostile in-process code; this blocks accidental bypasses.
No maintenance authorization, hardware operations or writer integration.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .config_maintenance_barrier import AdmissionRefused


class GuardedWorkerExecutor:
    """Optional single-worker dispatcher with no public submission channel."""

    def __init__(self, *, thread_name_prefix="player-control"):
        self.__pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=thread_name_prefix)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.shutdown(wait=True)
        return False

    @property
    def _max_workers(self):
        """Internal shape check for the offline single-worker fence."""
        return 1

    def shutdown(self, wait=True, *, cancel_futures=False):
        return self.__pool.shutdown(
            wait=wait, cancel_futures=cancel_futures)

    def submit(self, fn, /, *args, **kwargs):
        raise AdmissionRefused("direct worker submission is disabled")

    def _submit_accounted(self, operation, *args):
        """Only OfflineMaintenanceBarrier.submit should call this under lock."""
        return self.__pool.submit(operation, *args)

    def _submit_idle_fence(self, operation, *args):
        """Only the offline fence should call this after beginning drain."""
        return self.__pool.submit(operation, *args)
