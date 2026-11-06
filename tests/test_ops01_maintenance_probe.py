"""OPS-01 local one-shot rehearsal; synthetic socket, controller and worker only."""
import ast
import inspect
import threading
import unittest

import player_service
from test_ops01_live_admission import FakeController, LiveAdmissionTests, request
from tools.config_live_admission import InternalMaintenanceControl
from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)
from tools.config_maintenance_probe import OneShotMaintenanceProbe


class OneShotProbeTests(unittest.TestCase):
    def test_one_shot_idle_probe_reopens_socket_commands(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        probe = OneShotMaintenanceProbe(control)
        controller = FakeController()

        def exercise(path):
            self.assertFalse(probe.report()["consumed"])
            self.assertTrue(request(path, "help")["ok"])
            result = probe.run_once(timeout=2)
            self.assertEqual(result["result"],
                             "idle_fence_and_clean_abort_observed")
            self.assertEqual(barrier.state, BarrierState.OPEN)
            self.assertTrue(request(path, "status")["ok"])
            self.assertTrue(request(path, "help")["ok"])
            self.assertFalse(result["write_authorized"])
            self.assertFalse(result["writer_exclusion_verified"])
            self.assertFalse(result["production_maintenance_authorized"])
            self.assertTrue(probe.report()["consumed"])
            with self.assertRaises(AdmissionRefused):
                probe.run_once(timeout=2)

        LiveAdmissionTests.service(self, controller, barrier, control, exercise)

    def test_playing_controller_fails_closed_without_automatic_cancel(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController("PLAYING")
        probe = OneShotMaintenanceProbe(control)

        def exercise(path):
            with self.assertRaises(AdmissionRefused):
                probe.run_once(timeout=2)
            self.assertEqual(barrier.state, BarrierState.CLOSED)
            self.assertFalse(request(path, "status")["ok"])
            self.assertEqual(request(path, "help")["error"],
                             "maintenance admission closed")
            with self.assertRaises(AdmissionRefused):
                probe.run_once(timeout=2)
            self.assertFalse(probe.report()["write_authorized"])

        LiveAdmissionTests.service(self, controller, barrier, control, exercise)

    def test_timeout_consumes_probe_and_keeps_admission_closed(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController()
        probe = OneShotMaintenanceProbe(control)
        entered, release = threading.Event(), threading.Event()

        def slow_refresh():
            entered.set()
            release.wait(timeout=3)
        controller.refresh = slow_refresh

        def exercise(path):
            self.assertTrue(entered.wait(timeout=2))
            try:
                with self.assertRaises(AdmissionRefused):
                    probe.run_once(timeout=0.03)
                self.assertEqual(barrier.state, BarrierState.CLOSED)
                self.assertTrue(probe.report()["consumed"])
                with self.assertRaises(AdmissionRefused):
                    probe.run_once(timeout=2)
                self.assertEqual(request(path, "help")["error"],
                                 "maintenance admission closed")
            finally:
                release.set()
        LiveAdmissionTests.service(self, controller, barrier, control, exercise)

    def test_two_simultaneous_invocations_only_one_succeeds(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController()
        probe = OneShotMaintenanceProbe(control)

        def exercise(path):
            start = threading.Barrier(3)
            results = []
            def invoke():
                start.wait(timeout=2)
                try:
                    results.append(probe.run_once(timeout=2)["result"])
                except AdmissionRefused:
                    results.append("refused")
            threads = [threading.Thread(target=invoke) for _ in range(2)]
            for t in threads:
                t.start()
            start.wait(timeout=2)
            for t in threads:
                t.join(timeout=3)
                self.assertFalse(t.is_alive())
            self.assertEqual(sorted(results),
                             ["idle_fence_and_clean_abort_observed", "refused"])
            self.assertEqual(barrier.state, BarrierState.OPEN)
            self.assertTrue(request(path, "help")["ok"])
        LiveAdmissionTests.service(self, controller, barrier, control, exercise)

    def test_unbound_control_consumes_probe_and_never_retries(self):
        control = InternalMaintenanceControl(OfflineMaintenanceBarrier())
        probe = OneShotMaintenanceProbe(control)
        with self.assertRaises(AdmissionRefused):
            probe.run_once(timeout=0.1)
        self.assertTrue(probe.report()["consumed"])
        with self.assertRaises(AdmissionRefused):
            probe.run_once(timeout=0.1)

    def test_probe_accepts_only_exact_internal_control(self):
        for invalid in (None, object(), OfflineMaintenanceBarrier()):
            with self.subTest(value=type(invalid)):
                with self.assertRaises(TypeError):
                    OneShotMaintenanceProbe(invalid)

    def test_production_start_path_does_not_instantiate_probe(self):
        tree = ast.parse(inspect.getsource(player_service.run))
        names = [
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertNotIn("OneShotMaintenanceProbe", names)
        self.assertNotIn("InternalMaintenanceControl", names)
        self.assertNotIn("OfflineMaintenanceBarrier", names)

    def test_probe_has_no_writer_or_external_action_api(self):
        self.assertFalse(hasattr(OneShotMaintenanceProbe, "apply"))
        self.assertFalse(hasattr(OneShotMaintenanceProbe, "record_change_possible"))
        self.assertFalse(hasattr(OneShotMaintenanceProbe, "stop_service"))
        self.assertFalse(hasattr(OneShotMaintenanceProbe, "restart_service"))


if __name__ == "__main__":
    unittest.main()
