"""M0.19: cached wake state over a real temporary Unix socket.

No optical drive, GPIO, Sonos connection, or production socket.
"""
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from player_service import (
    StatusPublisher,
    process_drive_activity,
)
from integrations.outputs.oled_status import (
    drive_waking,
    drive_fault,
    request_status,
)


class FakeController:
    def __init__(self):
        self.server = SimpleNamespace(reader_active=lambda: False)

    def status(self):
        return "STOPPED"

    def status_snapshot(self):
        return {"player": {"mode": "STOPPED"}}


class FakePower:
    def __init__(self, entered, release, *, succeed=True):
        self.entered = entered
        self.release = release
        self.succeed = succeed
        self.idle_policy = SimpleNamespace(
            play_must_wait=False,
            wake_in_progress=False,
            wake_fault_latched=False,
        )

    def activity(self):
        self.idle_policy.play_must_wait = True
        self.idle_policy.wake_in_progress = True

    def wake(self, *, verify):
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("test wake gate timed out")
        self.idle_policy.wake_in_progress = False
        if not self.succeed:
            self.idle_policy.wake_fault_latched = True
            return False
        self.idle_policy.play_must_wait = False
        return True

    def snapshot(self, *, reader_active):
        return {
            "idle_waking": self.idle_policy.wake_in_progress,
            "idle_wake_fault_latched":
                self.idle_policy.wake_fault_latched,
        }


class WakeSocketSnapshotTests(unittest.TestCase):

    def run_scenario(self, *, succeed):
        entered = threading.Event()
        release = threading.Event()
        stop = threading.Event()
        results = []
        errors = []

        power = FakePower(entered, release, succeed=succeed)
        controller = FakeController()
        publisher = StatusPublisher(controller, power)

        with tempfile.TemporaryDirectory(prefix="m019-socket-") as tmp:
            path = str(Path(tmp) / "control.sock")

            with socket.socket(
                socket.AF_UNIX, socket.SOCK_STREAM
            ) as server:
                server.bind(path)
                server.listen(4)
                server.settimeout(0.1)

                def serve_status():
                    while not stop.is_set():
                        try:
                            conn, _ = server.accept()
                        except socket.timeout:
                            continue
                        except OSError:
                            break

                        with conn:
                            conn.settimeout(2)
                            try:
                                request = conn.recv(1024)
                                if request == b'{"command":"status"}\n':
                                    payload = publisher.read()
                                else:
                                    payload = {
                                        "ok": False,
                                        "error": "unexpected request",
                                    }
                                conn.sendall(
                                    json.dumps(payload).encode() + b"\n"
                                )
                            except Exception as exc:
                                errors.append(exc)

                def run_wake():
                    try:
                        ok = process_drive_activity(
                            power,
                            object(),
                            publish_wake=lambda:
                                publisher.publish(controller, power),
                        )
                        results.append(ok)
                        publisher.publish(controller, power)
                    except Exception as exc:
                        errors.append(exc)

                server_thread = threading.Thread(
                    target=serve_status, daemon=True
                )
                worker = threading.Thread(
                    target=run_wake, daemon=True
                )
                server_thread.start()

                try:
                    initial = request_status(path)
                    self.assertFalse(drive_waking(initial))

                    worker.start()
                    self.assertTrue(
                        entered.wait(2),
                        "Wake did not reach the simulated blocking gate",
                    )

                    # The worker is still blocked, yet the socket
                    # must serve the already published wake snapshot.
                    self.assertTrue(worker.is_alive())
                    during = request_status(path)
                    self.assertTrue(drive_waking(during))
                    self.assertFalse(drive_fault(during))

                    release.set()
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(results, [succeed])

                    after = request_status(path)
                    self.assertFalse(drive_waking(after))
                    self.assertEqual(
                        drive_fault(after), not succeed
                    )
                    self.assertEqual(errors, [])
                finally:
                    release.set()
                    stop.set()
                    worker.join(3) if worker.ident else None
                    server_thread.join(3)

    def test_status_remains_readable_during_successful_wake(self):
        self.run_scenario(succeed=True)

    def test_status_exposes_latched_fault_after_failed_wake(self):
        self.run_scenario(succeed=False)


if __name__ == "__main__":
    unittest.main()
