"""Actual player_service.serve() with fake devices and temporary socket."""

import json
import multiprocessing
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from integrations.outputs.oled_status import (
    drive_fault,
    drive_waking,
    request_status,
)
from player_state import Mode


class FakeController:
    def __init__(self):
        self.state = SimpleNamespace(mode=Mode.STOPPED)
        self.server = SimpleNamespace(
            reader_active=lambda: False,
            alive=lambda: False,
            device="/dev/fake-m019",
        )
        self.disc_status = None
        self.owned_url = None
        self._joined = False
        self._group_uncertain = False
        self.status_hook = None

    def status(self):
        return "STOPPED"

    def status_snapshot(self):
        return {"player": {"mode": "STOPPED"}}

    def refresh(self):
        return None


class FakeMonitor:
    def poll(self, controller):
        return False


class FakePower:
    def __init__(self, entered, release, succeed):
        self.entered = entered
        self.release = release
        self.succeed = succeed
        self.idle_policy = SimpleNamespace(
            play_must_wait=False,
            wake_in_progress=False,
            wake_fault_latched=False,
            stop_attempted=False,
            spun_down=False,
        )

    def activity(self):
        self.idle_policy.play_must_wait = True
        self.idle_policy.wake_in_progress = True

    def wake(self, *, verify):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("simulated wake timed out")

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

    def tick(self, **kwargs):
        return None


def run_fake_service(path, entered, release, succeed):
    # Fail immediately if an activity RPC accidentally takes the
    # ordinary transport or EJECT path.
    from unittest.mock import patch
    from player_service import serve

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected transport/EJECT operation")

    with patch("player_service.handle_command",
               side_effect=forbidden), \
         patch("player_service.eject_verified_idle",
               side_effect=forbidden):
        serve(
            FakeController(),
            path,
            poll_seconds=0.05,
            power=FakePower(entered, release, succeed),
            disc_monitor=FakeMonitor(),
        )


def rpc(path, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(path)
        conn.sendall(
            (json.dumps({"command": command}) + "\n").encode()
        )

        response = bytearray()
        while b"\n" not in response:
            chunk = conn.recv(4096)
            if not chunk:
                raise RuntimeError("socket closed before response")
            response.extend(chunk)

    return json.loads(response.split(b"\n", 1)[0])


class RealServeSocketTests(unittest.TestCase):
    def run_scenario(self, succeed):
        ctx = multiprocessing.get_context("fork")
        entered = ctx.Event()
        release = ctx.Event()
        results = []
        errors = []

        with tempfile.TemporaryDirectory(
            prefix="m019-real-serve-"
        ) as directory:
            path = str(Path(directory) / "control.sock")

            process = ctx.Process(
                target=run_fake_service,
                args=(path, entered, release, succeed),
                daemon=True,
            )
            process.start()

            client = None
            try:
                # Wait for the isolated service to become ready.
                deadline = time.monotonic() + 4
                while True:
                    if process.exitcode is not None:
                        self.fail(
                            f"fake service exited: {process.exitcode}"
                        )

                    try:
                        initial = request_status(path, timeout=0.3)
                        break
                    except (OSError, ValueError, TimeoutError):
                        if time.monotonic() >= deadline:
                            self.fail("fake service did not start")
                        time.sleep(0.02)

                self.assertFalse(drive_waking(initial))

                def send_activity():
                    try:
                        results.append(rpc(path, "drive-activity"))
                    except Exception as exc:
                        errors.append(exc)

                client = threading.Thread(
                    target=send_activity,
                    daemon=True,
                )
                client.start()

                self.assertTrue(
                    entered.wait(3),
                    "drive-activity did not enter simulated wake",
                )

                self.assertTrue(client.is_alive())

                # Real serve() must answer from its cached status
                # while its one controller worker is blocked.
                during = request_status(path, timeout=1.0)
                self.assertTrue(drive_waking(during))
                self.assertFalse(drive_fault(during))

                release.set()
                client.join(3)

                self.assertFalse(client.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(results), 1)
                self.assertIs(results[0]["ok"], succeed)

                deadline = time.monotonic() + 2
                while True:
                    after = request_status(path, timeout=1.0)
                    if not drive_waking(after):
                        break
                    if time.monotonic() >= deadline:
                        self.fail("wake status never cleared")
                    time.sleep(0.02)

                self.assertEqual(
                    drive_fault(after), not succeed
                )

                # No real drive or Sonos operation is possible:
                # both are replaced with fake interfaces.
                self.assertIsNone(process.exitcode)

            finally:
                release.set()
                if client is not None and client.ident is not None:
                    client.join(3)

                if process.is_alive():
                    process.terminate()
                process.join(3)

                if process.is_alive():
                    process.kill()
                    process.join(3)

    def test_real_serve_successful_wake(self):
        self.run_scenario(True)

    def test_real_serve_failed_wake_latches_fault(self):
        self.run_scenario(False)


if __name__ == "__main__":
    unittest.main()
