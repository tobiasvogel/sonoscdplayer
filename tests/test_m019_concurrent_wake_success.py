"""Successful concurrent wake RPCs through real serve(); fake devices only."""

import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from drive_idle_policy import IdleDrivePolicy
from integrations.outputs.oled_status import request_status
from player_service import response

from test_m019_concurrent_wake_rpc import (
    Controller,
    Monitor,
    rpc,
)


class VerifiedMonitor(Monitor):
    def validate_before_play(self, controller):
        return True


class SuccessfulPower:
    def __init__(self, entered, release, starts):
        self.entered = entered
        self.release = release
        self.starts = starts
        self.idle_policy = IdleDrivePolicy(
            idle_seconds=300,
            last_activity=0,
            spun_down=True,
            stop_attempted=True,
        )

    def activity(self):
        return self.idle_policy.activity(
            time.monotonic()
        ) == "wake"

    def wake(self, *, verify):
        with self.starts.get_lock():
            self.starts.value += 1

        self.entered.set()

        if not self.release.wait(5):
            raise RuntimeError("simulated wake timeout")

        self.idle_policy.wake_result(
            time.monotonic(),
            drive_verified=True,
        )
        return True

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


def run_service(path, entered, release, starts, plays):
    from player_service import serve

    controller = Controller()

    def fake_handle(ctrl, request, selection=None, eject=None):
        if request != {"command": "play-selected"}:
            raise AssertionError(
                "unexpected transport command"
            )

        p = power.idle_policy

        if (
            p.spun_down
            or p.wake_in_progress
            or p.wake_fault_latched
            or p.stop_attempted
        ):
            raise AssertionError(
                "PLAY reached handler before verified wake"
            )

        with plays.get_lock():
            plays.value += 1

        return response(ctrl, ok=True)

    def forbidden_eject(*args, **kwargs):
        raise AssertionError("EJECT must not run")

    power = SuccessfulPower(
        entered, release, starts
    )

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
            power=power,
            disc_monitor=VerifiedMonitor(),
        )


class SuccessfulConcurrentWakeTests(unittest.TestCase):

    def test_one_wake_before_queued_play_and_activity(self):
        ctx = multiprocessing.get_context("fork")

        entered = ctx.Event()
        release = ctx.Event()
        starts = ctx.Value("i", 0)
        plays = ctx.Value("i", 0)

        with tempfile.TemporaryDirectory(
            prefix="m019-wake-success-"
        ) as directory:

            path = str(
                Path(directory) / "control.sock"
            )

            process = ctx.Process(
                target=run_service,
                args=(
                    path, entered, release,
                    starts, plays,
                ),
                daemon=True,
            )
            process.start()

            replies = {}
            errors = {}
            threads = []

            def client(name, command, sent):
                try:
                    replies[name] = rpc(
                        path, command, sent
                    )
                except Exception as exc:
                    errors[name] = exc

            def start_client(name, command):
                sent = threading.Event()

                thread = threading.Thread(
                    target=client,
                    args=(name, command, sent),
                    daemon=True,
                )
                threads.append(thread)
                thread.start()

                self.assertTrue(sent.wait(2))

            try:
                deadline = time.monotonic() + 4

                while True:
                    if process.exitcode is not None:
                        self.fail(
                            "simulated service exited"
                        )

                    try:
                        request_status(
                            path, timeout=0.2
                        )
                        break

                    except (
                        OSError, ValueError,
                        TimeoutError,
                    ):
                        if time.monotonic() >= deadline:
                            self.fail(
                                "service did not start"
                            )
                        time.sleep(0.02)

                # First event starts the simulated wake.
                start_client(
                    "first", "drive-activity"
                )

                self.assertTrue(
                    entered.wait(3)
                )
                self.assertEqual(starts.value, 1)

                # Queue both commands while wake is blocked.
                start_client(
                    "play", "play-selected"
                )
                start_client(
                    "second", "drive-activity"
                )

                status = request_status(
                    path, timeout=1
                )
                self.assertTrue(
                    status["drive_power"][
                        "idle_waking"
                    ]
                )

                self.assertEqual(plays.value, 0)
                self.assertEqual(starts.value, 1)

                # Wake verification succeeds.
                release.set()

                for thread in threads:
                    thread.join(4)
                    self.assertFalse(
                        thread.is_alive()
                    )

                self.assertEqual(errors, {})
                self.assertEqual(
                    set(replies),
                    {"first", "play", "second"},
                )

                for reply in replies.values():
                    self.assertIs(
                        reply["ok"], True, reply
                    )

                self.assertEqual(starts.value, 1)
                self.assertEqual(plays.value, 1)

                final = request_status(
                    path, timeout=1
                )["drive_power"]

                self.assertFalse(
                    final["idle_waking"]
                )
                self.assertFalse(
                    final["idle_spun_down"]
                )
                self.assertFalse(
                    final["idle_wake_fault_latched"]
                )
                self.assertFalse(
                    final["idle_stop_attempted"]
                )

                self.assertIsNone(
                    process.exitcode
                )

            finally:
                release.set()

                for thread in threads:
                    if thread.ident is not None:
                        thread.join(3)

                if process.is_alive():
                    process.terminate()

                process.join(3)

                if process.is_alive():
                    process.kill()
                    process.join(3)


if __name__ == "__main__":
    unittest.main()
