"""OPS-01 offline FIFO fence tests; synthetic controller only."""
import concurrent.futures
import threading
import time
import types
import unittest

from tools.config_worker_idle_fence import freeze_after_worker_drain
from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)


class SyntheticServer:
    def __init__(self):
        self.proc = None
        self._reader_active = False

    def alive(self):
        return False

    def reader_active(self):
        return False


def idle_controller():
    return types.SimpleNamespace(
        state=types.SimpleNamespace(mode=types.SimpleNamespace(value="STOPPED")),
        owned_url=None, player=None, server=SyntheticServer(),
        _joined={}, _group_uncertain=False, _recovery_url=None,
        _group_coord=None, _group_members=frozenset(),
    )


class WorkerIdleFenceTests(unittest.TestCase):
    def test_drained_fifo_can_freeze_but_never_authorizes_write(self):
        gate = OfflineMaintenanceBarrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = freeze_after_worker_drain(gate, pool, idle_controller())
        self.assertEqual(result["state"], "FROZEN")
        self.assertFalse(result["write_authorized"])
        self.assertFalse(result["live_exclusion_verified"])

    def test_previously_queued_work_finishes_before_sample(self):
        gate = OfflineMaintenanceBarrier()
        ctrl = idle_controller()
        started, release = threading.Event(), threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def operation():
                started.set()
                self.assertTrue(release.wait(3))
                ctrl.state.mode.value = "PLAYING"

            old = gate.submit(pool, operation)
            self.assertTrue(started.wait(2))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as orchestrator:
                check = orchestrator.submit(
                    freeze_after_worker_drain, gate, pool, ctrl, timeout=4)
                end = time.monotonic() + 2
                while gate.state != BarrierState.DRAINING and time.monotonic() < end:
                    time.sleep(0.001)
                self.assertEqual(gate.state, BarrierState.DRAINING)
                with self.assertRaises(AdmissionRefused):
                    gate.submit(pool, lambda: None)
                release.set()
                old.result(timeout=3)
                with self.assertRaises(AdmissionRefused):
                    check.result(timeout=4)
        self.assertEqual(gate.state, BarrierState.DRAINING)

    def test_stale_or_joined_group_cannot_freeze(self):
        ctrl = idle_controller()
        ctrl._joined["synthetic"] = object()
        gate = OfflineMaintenanceBarrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(AdmissionRefused):
                freeze_after_worker_drain(gate, pool, ctrl)
        self.assertEqual(gate.state, BarrierState.DRAINING)

    def test_running_work_timeout_fails_closed(self):
        gate = OfflineMaintenanceBarrier()
        entered, release = threading.Event(), threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def blocking():
                entered.set()
                release.wait(2)
            running = gate.submit(pool, blocking)
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(AdmissionRefused):
                    freeze_after_worker_drain(
                        gate, pool, idle_controller(), timeout=0.01)
                self.assertEqual(gate.state, BarrierState.DRAINING)
                with self.assertRaises(AdmissionRefused):
                    gate.submit(pool, lambda: None)
            finally:
                release.set()
            running.result(timeout=3)

    def test_drain_cannot_overtake_executor_submission(self):
        gate = OfflineMaintenanceBarrier()
        entered, release = threading.Event(), threading.Event()
        outcome = []

        class SlowSubmission:
            def submit(self, operation, *args):
                entered.set()
                self_was_released = release.wait(3)
                if not self_was_released:
                    raise TimeoutError("synthetic submit stalled")
                future = concurrent.futures.Future()
                future.set_result(operation(*args))
                return future

        def submit_task():
            outcome.append(gate.submit(SlowSubmission(), lambda: 12).result())

        submitting = threading.Thread(target=submit_task)
        submitting.start()
        self.assertTrue(entered.wait(2))
        drain_finished = threading.Event()

        def drain():
            gate.begin_draining()
            drain_finished.set()

        draining = threading.Thread(target=drain)
        draining.start()
        try:
            # No deadlocking inspection of gate.state while its lock is held.
            # Drain cannot return before the queued submission is released.
            self.assertFalse(drain_finished.wait(timeout=0.02))
        finally:
            release.set()
            submitting.join(timeout=4)
            draining.join(timeout=4)
        self.assertFalse(submitting.is_alive())
        self.assertFalse(draining.is_alive())
        self.assertEqual(outcome, [12])
        self.assertEqual(gate.state, BarrierState.DRAINING)
        self.assertEqual(gate.in_flight, 0)

    def test_double_fence_is_denied(self):
        gate = OfflineMaintenanceBarrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            freeze_after_worker_drain(gate, pool, idle_controller())
            with self.assertRaises(AdmissionRefused):
                freeze_after_worker_drain(gate, pool, idle_controller())

    def test_invalid_timeout_does_not_change_admission(self):
        gate = OfflineMaintenanceBarrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ValueError):
                freeze_after_worker_drain(gate, pool, idle_controller(), timeout=0)
        self.assertEqual(gate.state, BarrierState.OPEN)


if __name__ == "__main__":
    unittest.main()
