"""OPS-01 internal live-loop admission tests using a synthetic controller/socket.

No real player run(), systemd, CD drive, Sonos or production config.
"""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from player_service import serve
from tools.config_guarded_worker import GuardedWorkerExecutor
from tools.config_live_admission import InternalMaintenanceControl
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
        self.status_hook = None
        self.refresh_count = 0

    def status(self):
        return "synthetic-idle"

    def status_snapshot(self):
        return {"schema_version": 1, "player": {"mode": self.state.mode.value}}

    def refresh(self):
        self.refresh_count += 1


def request(path, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(2)
        conn.connect(str(path))
        conn.sendall((json.dumps({"command": command}) + "\n").encode())
        payload = bytearray()
        while b"\n" not in payload:
            chunk = conn.recv(4096)
            if not chunk:
                break
            payload.extend(chunk)
    return json.loads(payload.split(b"\n", 1)[0])


class LiveAdmissionTests(unittest.TestCase):
    def service(self, controller, barrier, control, callback):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "synthetic.sock"
            shutdown = threading.Event()
            errors = []
            def run():
                try:
                    serve(controller, path, poll_seconds=0.02,
                          maintenance_barrier=barrier,
                          internal_maintenance_control=control,
                          test_shutdown_event=shutdown)
                except BaseException as exc:
                    errors.append(exc)
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            try:
                for _ in range(200):
                    if path.exists() and control.report()["attached"]:
                        break
                    if errors:
                        raise errors[0]
                    time.sleep(0.005)
                self.assertTrue(path.exists())
                self.assertTrue(control.report()["attached"])
                callback(path)
            finally:
                shutdown.set()
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                if errors:
                    raise errors[0]

    def test_live_loop_rejects_commands_and_keeps_cached_status(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController()
        def exercise(path):
            self.assertTrue(request(path, "help")["ok"])
            result = control.prepare(timeout=2)
            self.assertEqual(result["barrier_state"], "FROZEN")
            self.assertFalse(result["write_authorized"])
            self.assertTrue(request(path, "status")["ok"])
            self.assertEqual(request(path, "help")["error"],
                             "maintenance admission closed")
            self.assertEqual(control.cancel_before_change()["barrier_state"],
                             "OPEN")
            self.assertTrue(request(path, "help")["ok"])
        self.service(controller, barrier, control, exercise)

    def test_refreshes_stop_while_frozen_then_resume(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController()
        def exercise(path):
            for _ in range(100):
                if controller.refresh_count:
                    break
                time.sleep(0.01)
            self.assertGreater(controller.refresh_count, 0)
            control.prepare(timeout=2)
            during = controller.refresh_count
            time.sleep(0.10)
            self.assertEqual(controller.refresh_count, during)
            control.cancel_before_change()
            for _ in range(100):
                if controller.refresh_count > during:
                    break
                time.sleep(0.01)
            self.assertGreater(controller.refresh_count, during)
        self.service(controller, barrier, control, exercise)

    def test_playing_controller_cannot_be_quiesced_or_stopped(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController("PLAYING")
        def exercise(path):
            with self.assertRaises(AdmissionRefused):
                control.prepare(timeout=2)
            self.assertEqual(controller.state.mode.value, "PLAYING")
            self.assertFalse(control.report()["write_authorized"])
            self.assertFalse(request(path, "status")["ok"])
            self.assertEqual(request(path, "help")["error"],
                             "maintenance admission closed")
        self.service(controller, barrier, control, exercise)

    def test_concurrent_socket_requests_during_drain_are_rejected(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController()
        entered, release = threading.Event(), threading.Event()

        def blocked_refresh():
            entered.set()
            if not release.wait(3):
                raise AssertionError("synthetic refresh not released")
            controller.refresh_count += 1

        controller.refresh = blocked_refresh

        def exercise(path):
            self.assertTrue(entered.wait(2))
            finished, errors = [], []

            def prepare():
                try:
                    finished.append(control.prepare(timeout=2))
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=prepare)
            thread.start()
            try:
                end = time.monotonic() + 2
                while barrier.state == BarrierState.OPEN and time.monotonic() < end:
                    time.sleep(0.002)
                self.assertEqual(barrier.state, BarrierState.DRAINING)
                results = []
                threads = [
                    threading.Thread(target=lambda: results.append(request(path, "help")))
                    for _ in range(6)
                ]
                for worker in threads:
                    worker.start()
                for worker in threads:
                    worker.join(timeout=2)
                    self.assertFalse(worker.is_alive())
                self.assertEqual(len(results), 6)
                self.assertTrue(all(
                    item.get("error") == "maintenance admission closed"
                    for item in results))
                self.assertTrue(request(path, "status")["ok"])
                self.assertFalse(finished)
            finally:
                release.set()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, errors)
            self.assertEqual(finished[0]["barrier_state"], "FROZEN")
            self.assertEqual(control.cancel_before_change()["barrier_state"], "OPEN")
            self.assertTrue(request(path, "help")["ok"])
        self.service(controller, barrier, control, exercise)

    def test_cancel_during_prepare_is_refused_without_early_reopen(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController()
        entered, release = threading.Event(), threading.Event()

        def blocked_refresh():
            entered.set()
            release.wait(3)

        controller.refresh = blocked_refresh

        def exercise(path):
            self.assertTrue(entered.wait(2))
            outcomes = []

            def prepare():
                try:
                    outcomes.append(control.prepare(timeout=2))
                except BaseException as exc:
                    outcomes.append(exc)

            thread = threading.Thread(target=prepare)
            thread.start()
            try:
                end = time.monotonic() + 2
                while barrier.state == BarrierState.OPEN and time.monotonic() < end:
                    time.sleep(0.002)
                self.assertEqual(barrier.state, BarrierState.DRAINING)
                with self.assertRaises(AdmissionRefused):
                    control.cancel_before_change()
                self.assertEqual(barrier.state, BarrierState.DRAINING)
                self.assertEqual(request(path, "help")["error"],
                                 "maintenance admission closed")
            finally:
                release.set()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(outcomes), 1)
            self.assertIsInstance(outcomes[0], dict)
            self.assertEqual(outcomes[0]["barrier_state"], "FROZEN")
            control.cancel_before_change()
        self.service(controller, barrier, control, exercise)

    def test_concurrent_duplicate_prepare_refused(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController()
        entered, release = threading.Event(), threading.Event()

        def blocked_refresh():
            entered.set()
            release.wait(3)

        controller.refresh = blocked_refresh

        def exercise(path):
            self.assertTrue(entered.wait(2))
            errors = []

            def prepare():
                try:
                    control.prepare(timeout=2)
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=prepare)
            thread.start()
            try:
                end = time.monotonic() + 2
                while barrier.state == BarrierState.OPEN and time.monotonic() < end:
                    time.sleep(0.002)
                self.assertEqual(barrier.state, BarrierState.DRAINING)
                with self.assertRaises(AdmissionRefused):
                    control.prepare(timeout=0.1)
                self.assertEqual(request(path, "help")["error"],
                                 "maintenance admission closed")
            finally:
                release.set()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            control.cancel_before_change()
        self.service(controller, barrier, control, exercise)

    def test_refresh_timeout_closes_admission_and_prevents_resume(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController()
        entered, release = threading.Event(), threading.Event()

        def blocked_refresh():
            entered.set()
            release.wait(3)

        controller.refresh = blocked_refresh

        def exercise(path):
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(AdmissionRefused):
                    control.prepare(timeout=0.03)
                self.assertEqual(barrier.state, BarrierState.CLOSED)
                with self.assertRaises(AdmissionRefused):
                    control.cancel_before_change()
                self.assertEqual(request(path, "help")["error"],
                                 "maintenance admission closed")
                self.assertFalse(request(path, "status")["ok"])
                self.assertFalse(control.report()["write_authorized"])
            finally:
                release.set()
        self.service(controller, barrier, control, exercise)

    def test_simultaneous_abort_only_one_can_reopen(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        controller = FakeController()

        def exercise(path):
            control.prepare(timeout=2)
            start = threading.Barrier(3)
            outcomes = []

            def abort():
                start.wait(timeout=2)
                try:
                    outcomes.append(control.cancel_before_change()["barrier_state"])
                except AdmissionRefused:
                    outcomes.append("refused")

            threads = [threading.Thread(target=abort) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(sorted(outcomes), ["OPEN", "refused"])
            self.assertEqual(barrier.state, BarrierState.OPEN)
            self.assertTrue(request(path, "help")["ok"])
        self.service(controller, barrier, control, exercise)

    def test_control_requires_matching_injected_barrier(self):
        with tempfile.TemporaryDirectory() as root:
            controller = FakeController()
            first = OfflineMaintenanceBarrier()
            other = InternalMaintenanceControl(OfflineMaintenanceBarrier())
            with self.assertRaises(ValueError):
                serve(controller, Path(root) / "unused.sock",
                      maintenance_barrier=first,
                      internal_maintenance_control=other)
            self.assertFalse((Path(root) / "unused.sock").exists())

    def test_unbound_control_refuses_prepare(self):
        control = InternalMaintenanceControl(OfflineMaintenanceBarrier())
        with self.assertRaises(AdmissionRefused):
            control.prepare(timeout=0.01)
        with self.assertRaises(AdmissionRefused):
            control.cancel_before_change()

    def test_no_implicit_maintenance_control_in_service_signature(self):
        import inspect
        self.assertIsNone(inspect.signature(serve).parameters[
            "internal_maintenance_control"].default)
        self.assertIsNone(inspect.signature(serve).parameters[
            "test_shutdown_event"].default)

    def test_control_binding_is_one_time(self):
        barrier = OfflineMaintenanceBarrier()
        control = InternalMaintenanceControl(barrier)
        with GuardedWorkerExecutor() as executor:
            control.bind(executor, FakeController())
            with self.assertRaises(AdmissionRefused):
                control.bind(executor, FakeController())


if __name__ == "__main__":
    unittest.main()
