"""OPS-01 guarded-worker regression tests; synthetic work only."""
import concurrent.futures
import threading
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

    def test_superclass_submit_cannot_bypass_guard(self):
        ran = []
        with GuardedWorkerExecutor() as pool:
            self.assertNotIsInstance(pool, concurrent.futures.ThreadPoolExecutor)
            with self.assertRaises(TypeError):
                concurrent.futures.ThreadPoolExecutor.submit(
                    pool, lambda: ran.append("bypass"))
        self.assertEqual(ran, [])

    def test_cancelled_queued_job_cannot_freeze_while_running(self):
        barrier = OfflineMaintenanceBarrier()
        entered, release = threading.Event(), threading.Event()
        with GuardedWorkerExecutor() as pool:
            def blocking():
                entered.set()
                self.assertTrue(release.wait(3))
            current = submit_maintenance_work(pool, barrier, blocking)
            self.assertTrue(entered.wait(2))
            queued = submit_maintenance_work(pool, barrier, lambda: 42)
            self.assertTrue(queued.cancel())
            self.assertEqual(barrier.in_flight, 1)
            barrier.begin_draining()
            with self.assertRaises(AdmissionRefused):
                barrier.freeze(None)
            release.set()
            current.result(timeout=3)
        self.assertEqual(barrier.state, BarrierState.DRAINING)
        self.assertEqual(barrier.in_flight, 0)
        self.assertFalse(barrier.report()["write_authorized"])

    def test_shutdown_rejects_tracked_work_and_fence(self):
        barrier = OfflineMaintenanceBarrier()
        pool = GuardedWorkerExecutor()
        pool.shutdown(wait=True)
        with self.assertRaises(RuntimeError):
            submit_maintenance_work(pool, barrier, lambda: None)
        self.assertEqual(barrier.in_flight, 0)
        with self.assertRaisesRegex(AdmissionRefused, "could not be submitted"):
            freeze_after_worker_drain(barrier, pool, IdleController())
        self.assertEqual(barrier.state, BarrierState.DRAINING)
        self.assertFalse(barrier.report()["write_authorized"])

    def test_close_during_work_keeps_guard_closed(self):
        barrier = OfflineMaintenanceBarrier()
        entered, release = threading.Event(), threading.Event()
        with GuardedWorkerExecutor() as pool:
            def blocking():
                entered.set()
                self.assertTrue(release.wait(3))
                return 5
            current = submit_maintenance_work(pool, barrier, blocking)
            self.assertTrue(entered.wait(2))
            barrier.close()
            release.set()
            self.assertEqual(current.result(timeout=3), 5)
            with self.assertRaises(AdmissionRefused):
                pool.submit(lambda: 7)
            with self.assertRaises(AdmissionRefused):
                submit_maintenance_work(pool, barrier, lambda: 8)
            with self.assertRaises(AdmissionRefused):
                freeze_after_worker_drain(barrier, pool, IdleController())
        self.assertEqual(barrier.state, BarrierState.CLOSED)
        self.assertFalse(barrier.report()["write_authorized"])

    def test_default_executor_submission_remains_unchanged(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(submit_maintenance_work(
                pool, None, lambda: 13).result(timeout=2), 13)


if __name__ == "__main__":
    unittest.main()
