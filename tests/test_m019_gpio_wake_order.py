"""M0.19 one-shot synchronous wake ordering; no device I/O."""

import threading
import unittest

from integrations.interfaces.safe_button_execute import (
    SelectedTargetExecutor,
)


def snapshot(command):
    if command == "status":
        return {
            "ok": True,
            "state": {
                "schema_version": 1,
                "player": {"mode": "PLAYING"},
                "sonos": {
                    "owned": True,
                    "group_cleanup_pending": False,
                },
                "stream": {"active": True},
            },
        }
    if command == "get-target":
        return {
            "ok": True,
            "target_selection": {"selected": True},
        }
    raise AssertionError(command)


def run_action(action, *, wake_fails=False):
    calls = []
    finished = threading.Event()

    def sender(path, command, uid):
        calls.append(("send", command))
        if command == "drive-activity" and wake_fails:
            raise RuntimeError("wake rejected")
        return True

    def reader(path, command):
        calls.append(("read", command))
        return snapshot(command)

    executor = SelectedTargetExecutor(
        "/offline",
        reader=reader,
        sender=sender,
        report=lambda message: finished.set(),
        activity_before_read=True,
    )

    assert executor.submit(action)
    assert finished.wait(2), "executor did not finish"

    return calls


class WakeOrderTests(unittest.TestCase):

    def test_each_transport_button_wakes_before_reading(self):
        for action in ("playpause", "stop", "next", "previous"):
            with self.subTest(action=action):
                calls = run_action(action)

                self.assertEqual(
                    calls[0],
                    ("send", "drive-activity"),
                )
                self.assertEqual(
                    calls[1],
                    ("read", "status"),
                )
                self.assertEqual(
                    calls.count(("send", "drive-activity")),
                    1,
                )

    def test_failed_wake_blocks_following_commands(self):
        for action in ("playpause", "stop", "next", "previous"):
            with self.subTest(action=action):
                self.assertEqual(
                    run_action(action, wake_fails=True),
                    [("send", "drive-activity")],
                )

    def test_failed_wake_does_not_stop_eject_from_reaching_the_player_checks(self):
        calls = run_action("eject", wake_fails=True)
        self.assertEqual(calls, [("send", "drive-activity"), ("read", "status")])

    def test_eject_still_uses_separate_policy(self):
        calls = run_action("eject")
        self.assertNotIn(
            ("read", "get-target"),
            calls,
        )
        self.assertEqual(
            calls.count(("send", "drive-activity")),
            1,
        )


if __name__ == "__main__":
    unittest.main()
