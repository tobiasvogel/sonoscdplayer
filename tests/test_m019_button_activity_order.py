"""M0.19 button wake ordering; no GPIO, Sonos or hardware."""
import threading
import unittest
from unittest.mock import patch

from integrations.interfaces.safe_button_execute import (
    SelectedTargetExecutor,
)


class ButtonActivityOrderTests(unittest.TestCase):

    def execute(self, action, *, enabled=True, fail_activity=False):
        done = threading.Event()
        calls = []

        def sender(socket_path, command, uid):
            calls.append(("send", command))
            if command == "drive-activity" and fail_activity:
                raise RuntimeError("wake rejected")
            return True

        def reader(socket_path, command):
            calls.append(("read", command))
            return {"ok": True}

        def report(message):
            calls.append(("report", message))
            done.set()

        executor = SelectedTargetExecutor(
            "/tmp/not-connected-m019.sock",
            reader=reader,
            sender=sender,
            report=report,
            activity_before_read=enabled,
        )

        self.assertTrue(executor.submit(action))
        self.assertTrue(done.wait(3), "Executor did not finish")
        return calls

    def test_play_activity_precedes_every_status_read(self):
        with patch(
            "integrations.interfaces.safe_button_execute.plan_playpause",
            return_value="play-selected",
        ):
            calls = self.execute("playpause")

        self.assertEqual(calls[:4], [
            ("send", "drive-activity"),
            ("read", "status"),
            ("read", "get-target"),
            ("send", "play-selected"),
        ])

    def test_failed_activity_blocks_reads_and_transport(self):
        calls = self.execute("playpause", fail_activity=True)

        self.assertEqual(calls[0], ("send", "drive-activity"))
        self.assertFalse(any(
            kind == "read" for kind, _ in calls
        ))
        self.assertFalse(any(
            kind == "send" and command != "drive-activity"
            for kind, command in calls
        ))

    def test_disabled_feature_preserves_old_behavior(self):
        with patch(
            "integrations.interfaces.safe_button_execute.plan_playpause",
            return_value="play-selected",
        ):
            calls = self.execute("playpause", enabled=False)

        self.assertEqual(calls[0], ("read", "status"))
        self.assertNotIn(
            ("send", "drive-activity"), calls
        )

    def test_eject_does_not_inherit_play_wake_preflight(self):
        with patch(
            "integrations.interfaces.safe_button_execute.plan_eject",
            return_value="eject",
        ):
            calls = self.execute("eject")

        self.assertEqual(calls[0], ("read", "status"))
        self.assertNotIn(
            ("send", "drive-activity"), calls
        )
        self.assertIn(("send", "eject"), calls)


if __name__ == "__main__":
    unittest.main()
