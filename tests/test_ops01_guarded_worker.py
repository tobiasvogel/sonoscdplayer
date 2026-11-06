"""OPS-01 guarded-worker regression tests; synthetic work only."""
import concurrent.futures
import unittest

from player_service import submit_maintenance_work
from tools.config_guarded_worker import GuardedWorkerExecutor
from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)
from tools.config_worker_idle_fence import freeze_after_worker_drain


class IdleServer:
    proc = None
    _reader_active = False

    def alive(self):
        return False

    def reader_active(self):
        return False


class IdleController:
    class State:
        class Mode:
            value = "STOPPED"
        mode = Mode()
    state = State()
    owned_url = None
    player = None
    server = IdleServer()
    _joined = {}
    _group_uncertain = False
    _recovery_url = None
    _group_coord = None
    _group_members = frozenset()


class GuardedWorkerTests(unittest.TestCase):
    def test_direct_submit_is_rejected_without_running_work(self):
        ran = []
        with GuardedWorkerExecutor() as pool:
            with self.assertRaisesRegex(AdmissionRefused, "direct worker"):
                pool.submit(lambda: ran.append("bypassed"))
        self.assertEqual(ran, [])

    def test_tracked_submission_and_fence_share_one_worker(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as pool:
            future = submit_maintenance_work(pool, barrier, lambda: 9)
            self.assertEqual(future.result(timeout=2), 9)
            report = freeze_after_worker_drain(
                barrier, pool, IdleController(), timeout=3)
            self.assertEqual(report["state"], "FROZEN")
            self.assertFalse(report["write_authorized"])
            self.assertFalse(report["live_exclusion_verified"])

    def test_direct_submit_is_rejected_after_draining(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as pool:
            submit_maintenance_work(pool, barrier, lambda: None).result(2)
            barrier.begin_draining()
            with self.assertRaises(AdmissionRefused):
                pool.submit(lambda: None)
            with self.assertRaises(AdmissionRefused):
                submit_maintenance_work(pool, barrier, lambda: None)
        self.assertEqual(barrier.state, BarrierState.DRAINING)
        self.assertFalse(barrier.report()["write_authorized"])

    def test_failed_work_still_releases_ticket(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as pool:
            def fail():
                raise RuntimeError("synthetic failure")
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                submit_maintenance_work(pool, barrier, fail).result(timeout=2)
        self.assertEqual(barrier.in_flight, 0)

    def test_mismatched_guarded_executor_cannot_fence(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as first, GuardedWorkerExecutor() as other:
            submit_maintenance_work(first, barrier, lambda: None).result(2)
            with self.assertRaisesRegex(AdmissionRefused, "executor mismatch"):
                freeze_after_worker_drain(barrier, other, IdleController())
            self.assertEqual(barrier.state, BarrierState.OPEN)

    def test_default_executor_submission_remains_unchanged(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(submit_maintenance_work(
                pool, None, lambda: 13).result(timeout=2), 13)


if __name__ == "__main__":
    unittest.main()
