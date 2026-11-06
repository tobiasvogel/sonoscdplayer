"""OPS-01 service-loop retirement races, synthetic controller and socket only."""
import threading
import tempfile
import time
from pathlib import Path
import unittest

from player_service import serve
from test_ops01_live_admission import FakeController, request
from tools.config_live_admission import InternalMaintenanceControl
from tools.config_maintenance_probe import OneShotMaintenanceProbe
from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)


class ServiceRetirementTests(unittest.TestCase):
    def run_service(self, controller, callback):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        shutdown = threading.Event()
        errors = []
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.sock"

            def serve_thread():
                try:
                    serve(controller, path, poll_seconds=0.02,
                          maintenance_barrier=barrier,
                          internal_maintenance_control=control,
                          test_shutdown_event=shutdown)
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=serve_thread)
            thread.start()
            try:
                until = time.monotonic() + 3
                while not control.report()["attached"] and time.monotonic() < until:
                    if errors:
                        raise errors[0]
                    time.sleep(0.005)
                self.assertTrue(control.report()["attached"])
                callback(path, barrier, control, shutdown, thread)
            finally:
                shutdown.set()
                thread.join(timeout=4)
                self.assertFalse(thread.is_alive(), "synthetic service did not exit")
            if errors:
                raise errors[0]
        self.assertEqual(barrier.state, BarrierState.CLOSED)
        self.assertTrue(control.report()["retired"])
        with self.assertRaises(AdmissionRefused):
            control.prepare(timeout=0.1)
        with self.assertRaises(AdmissionRefused):
            control.cancel_before_change()
        return control

    def test_normal_service_exit_retires_control_without_probe(self):
        controller = FakeController()
        def exercise(path, barrier, control, shutdown, thread):
            self.assertTrue(request(path, "help")["ok"])
            shutdown.set()
        self.run_service(controller, exercise)

    def test_frozen_service_exit_blocks_late_cancel_and_rebind(self):
        controller = FakeController()
        def exercise(path, barrier, control, shutdown, thread):
            control.prepare(timeout=2)
            self.assertEqual(barrier.state, BarrierState.FROZEN)
            shutdown.set()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            with self.assertRaises(AdmissionRefused):
                control.cancel_before_change()
            self.assertEqual(barrier.state, BarrierState.CLOSED)
        control = self.run_service(controller, exercise)
        from tools.config_guarded_worker import GuardedWorkerExecutor
        with GuardedWorkerExecutor() as other:
            with self.assertRaises(AdmissionRefused):
                control.bind(other, FakeController())

    def test_service_exit_during_drain_prevents_late_probe_resume(self):
        controller = FakeController()
        entered, release = threading.Event(), threading.Event()
        def blocked_refresh():
            entered.set()
            release.wait(timeout=3)
        controller.refresh = blocked_refresh

        def exercise(path, barrier, control, shutdown, thread):
            self.assertTrue(entered.wait(2))
            probe = OneShotMaintenanceProbe(control)
            outcomes = []
            def run_probe():
                try:
                    outcomes.append(probe.run_once(timeout=2))
                except AdmissionRefused:
                    outcomes.append("refused")

            probe_thread = threading.Thread(target=run_probe)
            probe_thread.start()
            try:
                until = time.monotonic() + 2
                while barrier.state == BarrierState.OPEN and time.monotonic() < until:
                    time.sleep(0.002)
                self.assertEqual(barrier.state, BarrierState.DRAINING)
                shutdown.set()
                until = time.monotonic() + 2
                while barrier.state != BarrierState.CLOSED and time.monotonic() < until:
                    time.sleep(0.002)
                self.assertEqual(barrier.state, BarrierState.CLOSED)
            finally:
                release.set()
                probe_thread.join(timeout=3)
            self.assertFalse(probe_thread.is_alive())
            self.assertEqual(outcomes, ["refused"])
            self.assertTrue(probe.report()["consumed"])
            with self.assertRaises(AdmissionRefused):
                probe.run_once(timeout=0.1)
        self.run_service(controller, exercise)

    def test_late_one_shot_probe_after_service_exit_is_refused(self):
        controller = FakeController()
        holder = []
        def exercise(path, barrier, control, shutdown, thread):
            holder.append(OneShotMaintenanceProbe(control))
            shutdown.set()
        self.run_service(controller, exercise)
        with self.assertRaises(AdmissionRefused):
            holder[0].run_once(timeout=0.1)
        self.assertTrue(holder[0].report()["consumed"])

    def test_retirement_and_cancel_race_never_leaves_open_barrier(self):
        controller = FakeController()
        def exercise(path, barrier, control, shutdown, thread):
            control.prepare(timeout=2)
            self.assertEqual(barrier.state, BarrierState.FROZEN)
            start = threading.Barrier(3)
            outcomes = []
            def cancel():
                start.wait(timeout=2)
                try:
                    control.cancel_before_change()
                    outcomes.append("cancelled")
                except AdmissionRefused:
                    outcomes.append("refused")
            def retire():
                start.wait(timeout=2)
                shutdown.set()
            a = threading.Thread(target=cancel)
            b = threading.Thread(target=retire)
            a.start()
            b.start()
            start.wait(timeout=2)
            a.join(timeout=3)
            b.join(timeout=3)
            thread.join(timeout=3)
            self.assertFalse(a.is_alive())
            self.assertFalse(b.is_alive())
            self.assertFalse(thread.is_alive())
            self.assertEqual(barrier.state, BarrierState.CLOSED)
            self.assertIn(outcomes[0], ("cancelled", "refused"))
            with self.assertRaises(AdmissionRefused):
                control.cancel_before_change()
        self.run_service(controller, exercise)

    def test_retirement_is_idempotent_and_prevents_binding(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        control.retire()
        control.retire()
        self.assertTrue(control.report()["retired"])
        self.assertEqual(barrier.state, BarrierState.CLOSED)
        from tools.config_guarded_worker import GuardedWorkerExecutor
        with GuardedWorkerExecutor() as executor:
            with self.assertRaises(AdmissionRefused):
                control.bind(executor, FakeController())
        self.assertFalse(control.report()["write_authorized"])


if __name__ == "__main__":
    unittest.main()
