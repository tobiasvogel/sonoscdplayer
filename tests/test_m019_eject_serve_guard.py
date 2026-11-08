"""M0.19: fail-closed EJECT through actual serve(); fake hardware only."""

import json
import multiprocessing
import socket
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from player_state import Mode
from integrations.outputs.oled_status import request_status


class FakeController:
    def __init__(self):
        self.state = SimpleNamespace(mode=Mode.STOPPED)
        self.server = SimpleNamespace(
            reader_active=lambda: False,
            alive=lambda: False,
            device="/dev/fake-m019-eject",
        )
        self.disc_status = None
        self.owned_url = None
        self.player = None
        self._joined = False
        self._group_uncertain = False
        self.status_hook = None

    def status(self):
        return "STOPPED"

    def status_snapshot(self):
        return {"player": {"mode": "STOPPED"}}

    def refresh(self):
        pass


class FakeMonitor:
    def poll(self, controller):
        return False


class FakePower:
    def __init__(self, flags):
        self.idle_policy = (
            SimpleNamespace(**flags)
            if flags is not None else None
        )

    def snapshot(self, *, reader_active):
        policy = self.idle_policy
        return {
            "idle_stop_attempted": (
                bool(policy.stop_attempted) if policy else False
            ),
            "idle_spun_down": (
                bool(policy.spun_down) if policy else False
            ),
            "idle_waking": (
                bool(policy.wake_in_progress) if policy else False
            ),
            "idle_wake_fault_latched": (
                bool(policy.wake_fault_latched) if policy else False
            ),
        }

    def tick(self, **kwargs):
        pass

    def activity(self):
        raise AssertionError("EJECT must not request activity")

    def wake(self, **kwargs):
        raise AssertionError("EJECT must not start the motor")


def fake_service(path, flags, handler_calls):
    from player_service import serve, response

    controller = FakeController()

    def fake_handle(controller, request, selection=None, eject=None):
        with handler_calls.get_lock():
            handler_calls.value += 1
        return response(controller, ok=True)

    def forbidden_eject(*args, **kwargs):
        raise AssertionError("actual EJECT must never run in this test")

    with patch(
        "player_service.handle_command",
        side_effect=fake_handle,
    ), patch(
        "player_service.eject_verified_idle",
        side_effect=forbidden_eject,
    ):
        serve(
            controller,
            path,
            poll_seconds=0.05,
            power=FakePower(flags),
            disc_monitor=FakeMonitor(),
        )


def rpc(path, command):
    with socket.socket(
        socket.AF_UNIX, socket.SOCK_STREAM
    ) as conn:
        conn.settimeout(3)
        conn.connect(path)
        conn.sendall(
            (json.dumps({"command": command}) + "\n").encode()
        )
        data = bytearray()
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                raise RuntimeError("socket closed before reply")
            data.extend(chunk)

    return json.loads(data.split(b"\n", 1)[0])


class EjectServeGuardTests(unittest.TestCase):

    def flags(self, **overrides):
        values = {
            "stop_attempted": False,
            "spun_down": False,
            "wake_in_progress": False,
            "wake_fault_latched": False,
        }
        values.update(overrides)
        return values

    def scenario(self, flags, *, allowed=False, command="eject"):
        ctx = multiprocessing.get_context("fork")
        handler_calls = ctx.Value("i", 0)

        with tempfile.TemporaryDirectory(
            prefix="m019-eject-guard-"
        ) as directory:
            path = str(Path(directory) / "control.sock")
            process = ctx.Process(
                target=fake_service,
                args=(path, flags, handler_calls),
                daemon=True,
            )
            process.start()

            try:
                deadline = time.monotonic() + 4.0
                while True:
                    if process.exitcode is not None:
                        self.fail(
                            f"fake service exited: {process.exitcode}"
                        )
                    try:
                        request_status(path, timeout=0.2)
                        break
                    except (OSError, ValueError, TimeoutError):
                        if time.monotonic() >= deadline:
                            self.fail("fake service did not start")
                        time.sleep(0.02)

                result = rpc(path, command)

                self.assertIs(
                    result["ok"], allowed,
                    result,
                )

                if allowed:
                    self.assertEqual(handler_calls.value, 1)
                else:
                    self.assertIn(
                        "EJECT blocked", result.get("error", "")
                    )
                    self.assertEqual(handler_calls.value, 0)

                self.assertIsNone(process.exitcode)

            finally:
                if process.is_alive():
                    process.terminate()
                process.join(3)
                if process.is_alive():
                    process.kill()
                    process.join(3)

    def test_unconfirmed_stop_still_reaches_the_eject_handler(self):
        self.scenario(
            self.flags(stop_attempted=True),
            allowed=True,
        )

    def test_sleeping_drive_blocks_eject(self):
        self.scenario(
            self.flags(stop_attempted=True, spun_down=True)
        )

    def test_wake_in_progress_blocks_eject(self):
        self.scenario(
            self.flags(wake_in_progress=True)
        )

    def test_wake_fault_still_reaches_the_eject_handler(self):
        # The handler (INP-03 checks) is faked here; it decides on the real drive.
        self.scenario(
            self.flags(wake_fault_latched=True, spun_down=True),
            allowed=True,
        )

    def test_wake_in_progress_blocks_eject_even_with_a_fault(self):
        self.scenario(
            self.flags(wake_fault_latched=True, wake_in_progress=True)
        )

    def test_normal_awake_drive_reaches_inp03_handler(self):
        # The INP-03 handler itself is replaced by a fake.
        # Its detailed safety checks have their own tests.
        self.scenario(self.flags(), allowed=True)

    def test_disabled_feature_preserves_inp03_route(self):
        self.scenario(None, allowed=True)

    def test_normalized_eject_is_also_blocked(self):
        self.scenario(
            self.flags(spun_down=True),
            command=" EJECT ",
        )


if __name__ == "__main__":
    unittest.main()
