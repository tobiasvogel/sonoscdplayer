"""Synthetic regression tests for OPS-01 worker fence deadlines."""
import concurrent.futures
import math
import types
import unittest

from tools.config_worker_idle_fence import freeze_after_worker_drain
from tools.config_maintenance_barrier import OfflineMaintenanceBarrier, BarrierState


class FenceDeadlineTests(unittest.TestCase):
    def test_nonfinite_timeout_does_not_begin_drain(self):
        for timeout in (math.nan, math.inf, -math.inf):
            gate = OfflineMaintenanceBarrier()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ValueError):
                    freeze_after_worker_drain(gate, pool, None, timeout=timeout)
            self.assertEqual(gate.state, BarrierState.OPEN)
            self.assertFalse(gate.report()["write_authorized"])


if __name__ == "__main__":
    unittest.main()
