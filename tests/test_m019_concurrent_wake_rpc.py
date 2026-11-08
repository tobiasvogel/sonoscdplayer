"""Concurrent M0.19 RPCs; real serve loop, fake motor and Sonos."""

import json
import multiprocessing
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from player_state import Mode
from integrations.outputs.oled_status import request_status


class Controller:
    def __init__(self):
        self.state = SimpleNamespace(mode=Mode.STOPPED)
        self.server = SimpleNamespace(
            reader_active=lambda: False,
            alive=lambda: False,
            device="/dev/fake-m019-concurrent",
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
        return None


class Monitor:
    def poll(self, controller):
        return False


class Power:
    def __init__(self, entered, release, attempts):
        from drive_idle_policy import IdleDrivePolicy

        self.idle_policy = IdleDrivePolicy(
            idle_seconds=300, last_activity=0,
            spun_down=True, stop_attempted=True,
        )
        self.entered = entered
        self.release = release
        self.attempts = attempts

    def activity(self):
        return self.idle_policy.activity(time.monotonic()) == "wake"

    def wake(self, *, verify):
        if self.idle_policy.wake_fault_latched:
            return False

        with self.attempts.get_lock():
            self.attempts.value += 1

        self.entered.set()

        if not self.release.wait(5):
            raise RuntimeError("test wake timeout")

        self.idle_policy.wake_result(
            time.monotonic(), drive_verified=False
        )
        return False

    def snapshot(self, *, reader_active):
        p = self.idle_policy
        return {
            "idle_waking": p.wake_in_progress,
            "idle_wake_fault_latched": p.wake_fault_latched,
            "idle_spun_down": p.spun_down,
            "idle_stop_attempted": p.stop_attempted,
        }

    def tick(self, **kwargs):
        return None


def run_service(path, entered, release, attempts):
    from player_service import serve

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "transport or EJECT handler must not be called"
        )

    with patch("player_service.handle_command",
               side_effect=forbidden), \
         patch("player_service.eject_verified_idle",
               side_effect=forbidden):
        serve(
            Controller(),
            path,
            poll_seconds=0.05,
            power=Power(entered, release, attempts),
            disc_monitor=Monitor(),
        )


def rpc(path, command, sent=None):
    with socket.socket(
        socket.AF_UNIX, socket.SOCK_STREAM
    ) as conn:
        conn.settimeout(5)
        conn.connect(path)
        conn.sendall(
            (json.dumps({"command": command}) + "\n").encode()
        )
        if sent is not None:
            sent.set()

        data = bytearray()
        while b"\n" not in data:
            part = conn.recv(4096)
            if not part:
                raise RuntimeError("socket closed without reply")
            data.extend(part)

    return json.loads(data.split(b"\n", 1)[0])


class ConcurrentWakeTests(unittest.TestCase):

    def test_queued_play_and_activity_after_failed_wake(self):
        ctx = multiprocessing.get_context("fork")
        entered = ctx.Event()
        release = ctx.Event()
        attempts = ctx.Value("i", 0)

        with tempfile.TemporaryDirectory(
            prefix="m019-concurrent-"
        ) as directory:
            path = str(Path(directory) / "control.sock")
            process = ctx.Process(
                target=run_service,
                args=(path, entered, release, attempts),
                daemon=True,
            )
            process.start()

            threads = []
            replies = {}
            errors = {}

            def client(name, command, sent):
                try:
                    replies[name] = rpc(path, command, sent)
                except Exception as exc:
                    errors[name] = exc

            try:
                deadline = time.monotonic() + 4
                while True:
                    if process.exitcode is not None:
                        self.fail("fake service exited prematurely")
                    try:
                        request_status(path, timeout=0.2)
                        break
                    except (OSError, TimeoutError, ValueError):
                        if time.monotonic() >= deadline:
                            self.fail("fake service did not start")
                        time.sleep(0.02)

                first_sent = threading.Event()
                t = threading.Thread(
                    target=client,
                    args=("first", "drive-activity", first_sent),
                )
                threads.append(t)
                t.start()

                self.assertTrue(first_sent.wait(2))
                self.assertTrue(entered.wait(3))

                # Worker is blocked in the first attempted wake.
                self.assertTrue(t.is_alive())
                self.assertEqual(attempts.value, 1)

                queued = []
                for name, command in (
                    ("play", "play-selected"),
                    ("activity", "drive-activity"),
                ):
                    sent = threading.Event()
                    queued.append(sent)
                    t = threading.Thread(
                        target=client,
                        args=(name, command, sent),
                    )
                    threads.append(t)
                    t.start()

                for sent in queued:
                    self.assertTrue(sent.wait(2))

                # Cached status must remain responsive.
                state = request_status(path, timeout=1)
                self.assertTrue(
                    state["drive_power"]["idle_waking"]
                )

                release.set()

                for t in threads:
                    t.join(4)
                    self.assertFalse(t.is_alive())

                self.assertEqual(errors, {})
                self.assertEqual(
                    set(replies), {"first", "play", "activity"}
                )
                for name, reply in replies.items():
                    with self.subTest(command=name):
                        self.assertIs(reply["ok"], False)

                # Exactly one motor wake attempt, despite more commands.
                self.assertEqual(attempts.value, 1)

                final = request_status(path, timeout=1)
                self.assertFalse(
                    final["drive_power"]["idle_waking"]
                )
                self.assertTrue(
                    final["drive_power"]["idle_wake_fault_latched"]
                )
                self.assertIsNone(process.exitcode)

            finally:
                release.set()
                for t in threads:
                    if t.ident is not None:
                        t.join(3)
                if process.is_alive():
                    process.terminate()
                process.join(3)
                if process.is_alive():
                    process.kill()
                    process.join(3)


if __name__ == "__main__":
    unittest.main()
