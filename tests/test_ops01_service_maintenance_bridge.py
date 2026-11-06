"""OPS-01 inactive player-service bridge: synthetic worker integration only."""
import inspect
import threading
from types import SimpleNamespace
import unittest

from player_service import (
    admit_maintenance_work, serve, simulate_internal_maintenance_drain,
    submit_maintenance_work,
)
from tools.config_guarded_worker import GuardedWorkerExecutor
from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)


class FakeReader:
    proc = None
    _reader_active = False

    def alive(self):
        return False

    def reader_active(self):
        return False


class FakeController:
    def __init__(self):
        self.state = SimpleNamespace(mode=SimpleNamespace(value="STOPPED"))
        self.owned_url = None
        self.player = None
        self.server = FakeReader()
        self._joined = {}
        self._group_uncertain = False
        self._recovery_url = None
        self._group_coord = None
        self._group_members = frozenset()


class ServiceMaintenanceBridgeTests(unittest.TestCase):
    def test_normal_service_has_no_maintenance_barrier_by_default(self):
        self.assertIsNone(inspect.signature(serve).parameters[
            "maintenance_barrier"].default)

    def test_same_service_submission_route_and_idle_fence(self):
        barrier = OfflineMaintenanceBarrier()
        controller = FakeController()
        with GuardedWorkerExecutor() as executor:
            events = []
            result = submit_maintenance_work(
                executor, barrier, events.append, "tracked")
            result.result(timeout=2)
            self.assertEqual(events, ["tracked"])
            report = simulate_internal_maintenance_drain(
                barrier, executor, controller)
            self.assertEqual(report["state"], "FROZEN")
            self.assertEqual(report["in_flight"], 0)
            self.assertFalse(report["write_authorized"])
            self.assertFalse(report["production_maintenance_authorized"])
            self.assertEqual(barrier.state, BarrierState.FROZEN)

    def test_service_mutation_is_refused_after_internal_drain(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            simulate_internal_maintenance_drain(
                barrier, executor, FakeController())
            with self.assertRaises(AdmissionRefused):
                submit_maintenance_work(
                    executor, barrier, lambda: "unexpected")
            self.assertEqual(barrier.state, BarrierState.FROZEN)

    def test_cached_status_remains_read_only_during_frozen_state(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            simulate_internal_maintenance_drain(
                barrier, executor, FakeController())
            self.assertIsNone(admit_maintenance_work(
                barrier, {"command": "status"}))
            with self.assertRaises(AdmissionRefused):
                admit_maintenance_work(
                    barrier, {"command": "status", "unexpected": True})
            self.assertEqual(barrier.state, BarrierState.FROZEN)

    def test_playing_controller_is_rejected_without_stopping_it(self):
        barrier = OfflineMaintenanceBarrier()
        controller = FakeController()
        controller.state.mode.value = "PLAYING"
        with GuardedWorkerExecutor() as executor:
            with self.assertRaises(AdmissionRefused):
                simulate_internal_maintenance_drain(
                    barrier, executor, controller)
            self.assertEqual(controller.state.mode.value, "PLAYING")
            self.assertEqual(barrier.state, BarrierState.CLOSED)

    def test_inflight_service_work_blocks_fence_until_finished(self):
        barrier = OfflineMaintenanceBarrier()
        controller = FakeController()
        work_started = threading.Event()
        release = threading.Event()
        with GuardedWorkerExecutor() as executor:
            def still_running():
                work_started.set()
                release.wait(timeout=2)
            future = submit_maintenance_work(
                executor, barrier, still_running)
            self.assertTrue(work_started.wait(timeout=2))
            try:
                with self.assertRaises(AdmissionRefused):
                    simulate_internal_maintenance_drain(
                        barrier, executor, controller, timeout=0.01)
                self.assertEqual(barrier.state, BarrierState.CLOSED)
            finally:
                release.set()
            future.result(timeout=2)

    def test_no_maintenance_barrier_refuses_offline_drain(self):
        with GuardedWorkerExecutor() as executor:
            with self.assertRaises(TypeError):
                simulate_internal_maintenance_drain(
                    None, executor, FakeController())

    def test_standard_worker_is_not_an_offline_fence_executor(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.assertRaises(TypeError):
                simulate_internal_maintenance_drain(
                    OfflineMaintenanceBarrier(), executor, FakeController())


if __name__ == "__main__":
    unittest.main()
