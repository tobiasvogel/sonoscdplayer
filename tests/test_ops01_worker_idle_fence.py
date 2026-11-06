"""OPS-01 offline FIFO fence tests; synthetic controller only."""
import ast
import concurrent.futures
import inspect
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

    def test_multiworker_executor_rejected_before_draining(self):
        gate = OfflineMaintenanceBarrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            with self.assertRaisesRegex(AdmissionRefused, "single-worker"):
                freeze_after_worker_drain(gate, pool, idle_controller())
        self.assertEqual(gate.state, BarrierState.OPEN)

    def test_unknown_executor_rejected_before_draining(self):
        gate = OfflineMaintenanceBarrier()
        class UnverifiedExecutor:
            def submit(self, *args):
                raise AssertionError("must not submit")
        with self.assertRaises(AdmissionRefused):
            freeze_after_worker_drain(gate, UnverifiedExecutor(), idle_controller())
        self.assertEqual(gate.state, BarrierState.OPEN)

    def test_executor_shutdown_fails_closed(self):
        gate = OfflineMaintenanceBarrier()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        pool.shutdown(wait=True)
        with self.assertRaisesRegex(AdmissionRefused, "could not be submitted"):
            freeze_after_worker_drain(gate, pool, idle_controller())
        self.assertEqual(gate.state, BarrierState.DRAINING)
        self.assertFalse(gate.report()["write_authorized"])

    def test_idle_sample_error_fails_closed(self):
        gate = OfflineMaintenanceBarrier()
        ctrl = idle_controller()
        # Errors derived from BaseException are deliberately not swallowed
        # by the controller sampler's ordinary Exception fault handling.
        class BrokenState:
            @property
            def mode(self):
                raise AssertionError("synthetic probe failure")
        ctrl.state = BrokenState()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaisesRegex(AdmissionRefused, "sample failed"):
                freeze_after_worker_drain(gate, pool, ctrl)
        self.assertEqual(gate.state, BarrierState.DRAINING)
        self.assertFalse(gate.report()["write_authorized"])

    def test_player_loop_uses_only_accounted_worker_submissions(self):
        from player_service import serve
        tree = ast.parse(inspect.getsource(serve))
        submissions = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "submit_maintenance_work"
        ]
        self.assertEqual(len(submissions), 2)
        untracked = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "submit"
        ]
        self.assertEqual(untracked, [])

    def test_player_service_has_no_direct_worker_submit_outside_gateway(self):
        """Detect accidental bypasses added anywhere in the service module."""
        import player_service
        tree = ast.parse(inspect.getsource(player_service))
        sites = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for subnode in ast.walk(node):
                if (isinstance(subnode, ast.Call)
                        and isinstance(subnode.func, ast.Attribute)
                        and subnode.func.attr == "submit"):
                    sites.append((node.name, subnode.func.value))
        self.assertEqual(len(sites), 2)
        self.assertEqual(
            {name for name, _ in sites}, {"submit_maintenance_work"})
        self.assertEqual(
            {value.id for _, value in sites if isinstance(value, ast.Name)},
            {"executor", "barrier"})

    def test_service_worker_reference_never_escapes_submission_gateway(self):
        """All uses of the serve() worker must be controlled submissions."""
        from player_service import serve
        tree = ast.parse(inspect.getsource(serve))
        refs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "executor"
            and isinstance(node.ctx, ast.Load)
        ]
        submissions = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "submit_maintenance_work"
        ]
        self.assertEqual(len(submissions), 2)
        for call in submissions:
            self.assertGreaterEqual(len(call.args), 3)
            self.assertIsInstance(call.args[0], ast.Name)
            self.assertEqual(call.args[0].id, "executor")
            self.assertIsInstance(call.args[1], ast.Name)
            self.assertEqual(call.args[1].id, "maintenance_barrier")
        self.assertEqual(len(refs), len(submissions))
        # No other expression in serve can hand the pool to an untracked
        # submitter, including nested refresh and socket command handlers.
        self.assertEqual({id(node) for node in refs},
                         {id(call.args[0]) for call in submissions})

    def test_service_worker_gateway_has_only_explicit_enabled_disabled_paths(self):
        """The disabled default remains explicit; enabled work is ticketed."""
        from player_service import submit_maintenance_work
        tree = ast.parse(inspect.getsource(submit_maintenance_work))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "submit"
        ]
        self.assertEqual(len(calls), 2)
        targets = {
            node.func.value.id for node in calls
            if isinstance(node.func.value, ast.Name)
        }
        self.assertEqual(targets, {"executor", "barrier"})
        self.assertTrue(any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "barrier"
            for node in ast.walk(tree)))

    def test_player_worker_pool_is_single_threaded(self):
        from player_service import serve
        tree = ast.parse(inspect.getsource(serve))
        pools = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "ThreadPoolExecutor"
        ]
        self.assertEqual(len(pools), 1)
        workers = [
            item.value for item in pools[0].keywords if item.arg == "max_workers"
        ]
        self.assertEqual(len(workers), 1)
        self.assertIsInstance(workers[0], ast.Constant)
        self.assertEqual(workers[0].value, 1)

    def test_double_fence_is_denied(self):
        gate = OfflineMaintenanceBarrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            freeze_after_worker_drain(gate, pool, idle_controller())
            with self.assertRaises(AdmissionRefused):
                freeze_after_worker_drain(gate, pool, idle_controller())


    def test_fence_rejects_different_executor_before_draining(self):
        gate = OfflineMaintenanceBarrier()
        ctrl = idle_controller()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as first:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as other:
                self.assertEqual(gate.submit(first, lambda: 7).result(), 7)
                with self.assertRaisesRegex(AdmissionRefused, "executor mismatch"):
                    freeze_after_worker_drain(gate, other, ctrl)
                self.assertEqual(gate.state, BarrierState.OPEN)
                self.assertEqual(
                    freeze_after_worker_drain(gate, first, ctrl)["state"],
                    "FROZEN")
                self.assertFalse(gate.report()["write_authorized"])

    def test_second_worker_pool_cannot_submit_under_one_barrier(self):
        gate = OfflineMaintenanceBarrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as first:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as other:
                self.assertEqual(gate.submit(first, lambda: 1).result(), 1)
                with self.assertRaisesRegex(AdmissionRefused, "executor mismatch"):
                    gate.submit(other, lambda: 2)
                self.assertEqual(gate.state, BarrierState.OPEN)
                self.assertEqual(gate.submit(first, lambda: 3).result(), 3)

    def test_binding_rejected_after_drain(self):
        gate = OfflineMaintenanceBarrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            gate.begin_draining()
            with self.assertRaises(AdmissionRefused):
                gate.bind_executor(pool)
        self.assertEqual(gate.state, BarrierState.DRAINING)
        self.assertFalse(gate.report()["write_authorized"])

    def test_invalid_timeout_does_not_change_admission(self):
        gate = OfflineMaintenanceBarrier()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ValueError):
                freeze_after_worker_drain(gate, pool, idle_controller(), timeout=0)
        self.assertEqual(gate.state, BarrierState.OPEN)


if __name__ == "__main__":
    unittest.main()
