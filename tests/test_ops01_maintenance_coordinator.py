"""Offline-only worker-drain and idle fence regression tests."""
from concurrent.futures import Future
from types import SimpleNamespace
import threading
import unittest

from tools.config_guarded_worker import GuardedWorkerExecutor
from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)
from tools.config_maintenance_coordinator import drain_and_sample_offline


class FakeServer:
    proc = None
    _reader_active = False

    def alive(self):
        return False

    def reader_active(self):
        return False


class FakeController:
    def __init__(self, mode="STOPPED"):
        self.state = SimpleNamespace(mode=SimpleNamespace(value=mode))
        self.owned_url = None
        self.player = None
        self.server = FakeServer()
        self._joined = {}
        self._group_uncertain = False
        self._recovery_url = None
        self._group_coord = None
        self._group_members = frozenset()


class OfflineCoordinatorTests(unittest.TestCase):
    def test_idle_freezes_only_offline_barrier(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            result = drain_and_sample_offline(barrier, executor,
                                              FakeController())
            self.assertEqual(result["state"], "FROZEN")
            self.assertEqual(result["in_flight"], 0)
            self.assertTrue(result["controller_idle_sampled_on_worker"])
            self.assertFalse(result["production_maintenance_authorized"])
            self.assertFalse(result["write_authorized"])
            self.assertFalse(result["live_exclusion_verified"])
            with self.assertRaises(AdmissionRefused):
                barrier.submit(executor, lambda: None)

    def test_playing_controller_is_refused_and_closed(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            with self.assertRaises(AdmissionRefused):
                drain_and_sample_offline(
                    barrier, executor, FakeController(mode="PLAYING"))
        self.assertEqual(barrier.state, BarrierState.CLOSED)

    def test_active_reader_is_refused(self):
        barrier = OfflineMaintenanceBarrier()
        controller = FakeController()
        controller.server._reader_active = True
        with GuardedWorkerExecutor() as executor:
            with self.assertRaises(AdmissionRefused):
                drain_and_sample_offline(barrier, executor, controller)
        self.assertEqual(barrier.state, BarrierState.CLOSED)

    def test_preexisting_work_drains_before_sampling(self):
        barrier = OfflineMaintenanceBarrier()
        started = threading.Event()
        release = threading.Event()
        controller = FakeController()
        with GuardedWorkerExecutor() as executor:
            def operation():
                started.set()
                self.assertTrue(release.wait(timeout=2))
                controller.state.mode.value = "STOPPED"
            controller.state.mode.value = "PLAYING"
            barrier.submit(executor, operation)
            self.assertTrue(started.wait(timeout=2))
            release.set()
            report = drain_and_sample_offline(
                barrier, executor, controller, timeout=2)
            self.assertEqual(report["state"], "FROZEN")

    def test_unfinished_tracked_work_times_out_closed(self):
        barrier = OfflineMaintenanceBarrier()
        started = threading.Event()
        release = threading.Event()
        with GuardedWorkerExecutor() as executor:
            def operation():
                started.set()
                release.wait(timeout=2)
            barrier.submit(executor, operation)
            self.assertTrue(started.wait(timeout=2))
            try:
                with self.assertRaises(AdmissionRefused):
                    drain_and_sample_offline(
                        barrier, executor, FakeController(), timeout=0.01)
                self.assertEqual(barrier.state, BarrierState.CLOSED)
            finally:
                release.set()

    def test_untracked_direct_submission_is_refused(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            with self.assertRaises(AdmissionRefused):
                executor.submit(lambda: None)
            self.assertEqual(barrier.state, BarrierState.OPEN)

    def test_wrong_executor_or_barrier_is_refused(self):
        with self.assertRaises(TypeError):
            drain_and_sample_offline(None, None, FakeController())

    def test_reentry_cannot_reopen_frozen_barrier(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            drain_and_sample_offline(barrier, executor, FakeController())
            with self.assertRaises(AdmissionRefused):
                drain_and_sample_offline(barrier, executor, FakeController())
            self.assertEqual(barrier.state, BarrierState.FROZEN)


if __name__ == "__main__":
    unittest.main()
