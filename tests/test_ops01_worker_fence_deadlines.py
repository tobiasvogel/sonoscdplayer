"""Synthetic regression tests for OPS-01 worker fence deadlines."""
import concurrent.futures
import math
import types
from unittest.mock import patch
import unittest

from tools import config_worker_idle_fence as fence
from tools.config_worker_idle_fence import freeze_after_worker_drain
from tools.config_maintenance_barrier import OfflineMaintenanceBarrier, BarrierState, AdmissionRefused


class FenceDeadlineTests(unittest.TestCase):
    def test_nonfinite_timeout_does_not_begin_drain(self):
        for timeout in (math.nan, math.inf, -math.inf):
            gate = OfflineMaintenanceBarrier()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ValueError):
                    freeze_after_worker_drain(gate, pool, None, timeout=timeout)
            self.assertEqual(gate.state, BarrierState.OPEN)
            self.assertFalse(gate.report()["write_authorized"])



    def test_expired_deadline_after_sample_fails_closed(self):
        gate = OfflineMaintenanceBarrier()
        controller = types.SimpleNamespace()
        # The sample result is synthetic: only the deadline path is tested.
        sample = types.SimpleNamespace(no_pending_external_activity=False)
        ticks = iter((0.0, 0.0, 0.2))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(fence, "sample_controller_idle", return_value=sample):
                with patch.object(fence, "time",
                                  types.SimpleNamespace(monotonic=lambda: next(ticks))):
                    with self.assertRaisesRegex(AdmissionRefused, "deadline expired"):
                        freeze_after_worker_drain(
                            gate, pool, controller, timeout=0.1)
        self.assertEqual(gate.state, BarrierState.DRAINING)
        self.assertFalse(gate.report()["write_authorized"])

if __name__ == "__main__":
    unittest.main()
