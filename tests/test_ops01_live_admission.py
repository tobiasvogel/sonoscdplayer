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
    AdmissionRefused, OfflineMaintenanceBarrier,
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
