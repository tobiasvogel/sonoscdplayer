"""Offline OPS-01 maintenance lifecycle; no service, hardware or file writes."""
import threading
from types import SimpleNamespace
import unittest

from tools.config_guarded_worker import GuardedWorkerExecutor
from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)
from tools.config_maintenance_lifecycle import (
    LifecycleState, OfflineMaintenanceLifecycle,
)


class FakeReader:
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
        self.server = FakeReader()
        self._joined = {}
        self._group_uncertain = False
        self._recovery_url = None
        self._group_coord = None
        self._group_members = frozenset()


class MaintenanceLifecycleTests(unittest.TestCase):
    def test_frozen_prechange_abort_restores_tracked_admission(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)
            report = session.prepare(FakeController())
            self.assertEqual(report["barrier_state"], "FROZEN")
            self.assertTrue(report["safe_abort_available"])
            self.assertFalse(report["write_authorized"])
            self.assertEqual(session.cancel_before_change()["barrier_state"], "OPEN")
            self.assertEqual(session.state, LifecycleState.NORMAL)
            self.assertEqual(barrier.submit(executor, lambda: 42).result(), 42)

    def test_repeated_clean_maintenance_can_prepare_again(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)
            session.prepare(FakeController())
            session.cancel_before_change()
            session.prepare(FakeController())
            self.assertEqual(barrier.state, BarrierState.FROZEN)

    def test_cancel_requires_completed_fence(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)
            with self.assertRaises(AdmissionRefused):
                session.cancel_before_change()
            self.assertEqual(barrier.state, BarrierState.OPEN)

    def test_playing_controller_fails_closed_not_reopenable(self):
        barrier = OfflineMaintenanceBarrier()
        controller = FakeController("PLAYING")
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)
            with self.assertRaises(AdmissionRefused):
                session.prepare(controller)
            self.assertEqual(controller.state.mode.value, "PLAYING")
            self.assertEqual(barrier.state, BarrierState.CLOSED)
            self.assertEqual(session.state, LifecycleState.HALTED)
            with self.assertRaises(AdmissionRefused):
                session.cancel_before_change()

    def test_timeout_with_running_operation_never_resumes(self):
        barrier = OfflineMaintenanceBarrier()
        started, release = threading.Event(), threading.Event()
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)

            def busy():
                started.set()
                release.wait(timeout=2)

            future = barrier.submit(executor, busy)
            self.assertTrue(started.wait(timeout=2))
            try:
                with self.assertRaises(AdmissionRefused):
                    session.prepare(FakeController(), timeout=0.01)
                self.assertEqual(barrier.state, BarrierState.CLOSED)
                with self.assertRaises(AdmissionRefused):
                    session.cancel_before_change()
            finally:
                release.set()
            future.result(timeout=2)

    def test_possible_change_prevents_resume_even_without_actual_write(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)
            session.prepare(FakeController())
            report = session.record_change_possible()
            self.assertEqual(report["lifecycle_state"], "CHANGE_POSSIBLE")
            self.assertTrue(report["recovery_review_required"])
            self.assertFalse(report["write_authorized"])
            with self.assertRaises(AdmissionRefused):
                session.cancel_before_change()
            self.assertEqual(barrier.state, BarrierState.FROZEN)
            self.assertEqual(session.finish_after_external_review()["barrier_state"],
                             "CLOSED")

    def test_mark_change_before_fence_refused(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)
            with self.assertRaises(AdmissionRefused):
                session.record_change_possible()

    def test_double_prepare_is_refused_during_quiescence(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)
            session.prepare(FakeController())
            with self.assertRaises(AdmissionRefused):
                session.prepare(FakeController())

    def test_bound_worker_must_match_on_reopen(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)
            session.prepare(FakeController())
            with GuardedWorkerExecutor() as other:
                with self.assertRaises(AdmissionRefused):
                    barrier.reopen_before_change(other)
            self.assertEqual(barrier.state, BarrierState.FROZEN)
            session.cancel_before_change()

    def test_closed_barrier_never_reopens(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            barrier.bind_executor(executor)
            barrier.close()
            with self.assertRaises(AdmissionRefused):
                barrier.reopen_before_change(executor)
            self.assertEqual(barrier.state, BarrierState.CLOSED)

    def test_reports_are_non_authorizing(self):
        barrier = OfflineMaintenanceBarrier()
        with GuardedWorkerExecutor() as executor:
            session = OfflineMaintenanceLifecycle(barrier, executor)
            for report in (session.report(), session.prepare(FakeController()),
                           session.cancel_before_change()):
                self.assertIs(report["write_authorized"], False)
                self.assertIs(report["production_maintenance_authorized"], False)
                self.assertIs(report["service_restart_authorized"], False)


if __name__ == "__main__":
    unittest.main()
