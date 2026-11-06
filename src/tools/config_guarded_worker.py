"""OPS-01 guarded single-worker executor for optional offline admission.

The normal submit() interface refuses work. Only the internal barrier and
worker-idle fence use the deliberately separate submission methods. This is
an accidental-bypass guard, not a security boundary against Python code with
access to private attributes or the underlying ThreadPoolExecutor API.
No maintenance authorization, hardware operations or writer integration.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .config_maintenance_barrier import AdmissionRefused


class GuardedWorkerExecutor(ThreadPoolExecutor):
    """Single-worker pool with no public direct submission path."""

    def __init__(self, *, thread_name_prefix="player-control"):
        super().__init__(max_workers=1, thread_name_prefix=thread_name_prefix)

    def submit(self, fn, /, *args, **kwargs):
        raise AdmissionRefused("direct worker submission is disabled")

    def _submit_accounted(self, operation, *args):
        """Called only by OfflineMaintenanceBarrier.submit under its lock."""
        return super().submit(operation, *args)

    def _submit_idle_fence(self, operation, *args):
        """Called only by the offline fence after begin_draining()."""
        return super().submit(operation, *args)
