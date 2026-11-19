"""Button activity wakes the drive: GPIO activity, wake mapping and ordering (no hardware)."""
import threading
import unittest
from unittest.mock import patch
from sonoscdplayer.hardware.inputs.play_button_execute import SelectedTargetExecutor
from sonoscdplayer.hardware.inputs.button_service import (
    DriveActivityPublisher,
    should_publish_drive_activity,
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
            "/tmp/not-connected.sock",
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
            "sonoscdplayer.hardware.inputs.play_button_execute.plan_playpause",
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
            "sonoscdplayer.hardware.inputs.play_button_execute.plan_playpause",
            return_value="play-selected",
        ):
            calls = self.execute("playpause", enabled=False)

        self.assertEqual(calls[0], ("read", "status"))
        self.assertNotIn(
            ("send", "drive-activity"), calls
        )

    def test_eject_wakes_before_separate_eject_policy(self):
        with patch(
            "sonoscdplayer.hardware.inputs.play_button_execute.plan_eject",
            return_value="eject",
        ):
            calls = self.execute("eject")

        self.assertEqual(calls[:3], [
            ("send", "drive-activity"),
            ("read", "status"),
            ("send", "eject"),
        ])
        self.assertEqual(
            calls.count(("send", "drive-activity")), 1
        )

    def test_failed_eject_wake_does_not_keep_the_disc_inside(self):
        # The wake failed (latched fault), but EJECT still goes on to the
        # player, which re-verifies the drive before touching the tray.
        with patch(
            "sonoscdplayer.hardware.inputs.play_button_execute.plan_eject",
            return_value="eject",
        ):
            calls = self.execute("eject", fail_activity=True)
        self.assertEqual(calls[0], ("send", "drive-activity"))
        self.assertIn(("read", "status"), calls)
        self.assertIn(("send", "eject"), calls)
        self.assertNotIn(("read", "get-target"), calls)

    def test_failed_wake_still_blocks_every_other_command(self):
        for action in ("playpause", "stop", "next", "previous"):
            with self.subTest(action=action):
                calls = self.execute(action, fail_activity=True)
                self.assertEqual(
                    [c for c in calls if c[0] != "report"],
                    [("send", "drive-activity")])


class ActivitySelectionTests(unittest.TestCase):

    def test_encoder_movement_reports_activity(self):
        for event in ('left', 'right'):
            self.assertTrue(
                should_publish_drive_activity(
                    'encoder', event, safe_executor=True
                )
            )

    def test_releases_are_ignored(self):
        self.assertFalse(
            should_publish_drive_activity(
                'stop', 'released', safe_executor=True
            )
        )

    def test_play_press_has_no_duplicate_rpc(self):
        # This test uses the project's actual button mapping.
        from sonoscdplayer.hardware.inputs.button_commands import map_event

        candidates = (
            'playpause', 'play_pause', 'play',
            'play_pause_button',
        )
        names = [
            name for name in candidates
            if map_event(name, 'pressed') == 'playpause'
        ]

        if not names:
            self.skipTest(
                'PLAY/PAUSE button name requires separate mapping inspection'
            )

        for name in names:
            self.assertFalse(
                should_publish_drive_activity(
                    name, 'pressed', safe_executor=True
                )
            )
            self.assertTrue(
                should_publish_drive_activity(
                    name, 'pressed', safe_executor=False
                )
            )

    def test_eject_activity_uses_single_wake_path(self):
        from sonoscdplayer.hardware.inputs.button_commands import map_event

        self.assertEqual(map_event('eject', 'pressed'), 'eject')
        self.assertFalse(
            should_publish_drive_activity(
                'eject', 'pressed', safe_executor=True
            )
        )
        self.assertTrue(
            should_publish_drive_activity(
                'eject', 'pressed', safe_executor=False
            )
        )
        for safe_executor in (False, True):
            self.assertFalse(
                should_publish_drive_activity(
                    'eject', 'released',
                    safe_executor=safe_executor,
                )
            )

    def test_stop_uses_single_synchronous_wake_path(self):
        from sonoscdplayer.hardware.inputs.button_commands import map_event

        self.assertEqual(map_event('stop', 'pressed'), 'stop')
        self.assertFalse(
            should_publish_drive_activity(
                'stop', 'pressed', safe_executor=True
            )
        )
        self.assertTrue(
            should_publish_drive_activity(
                'stop', 'pressed', safe_executor=False
            )
        )


class ActivityPublisherTests(unittest.TestCase):

    def test_publisher_sends_exact_activity_command(self):
        received = []
        done = threading.Event()

        def sender(socket_path, command, uid):
            received.append((socket_path, command, uid))
            done.set()
            return True

        pub = DriveActivityPublisher(
            '/tmp/not-connected.sock', sender=sender
        )
        pub.start()
        try:
            self.assertTrue(pub.enqueue())
            self.assertTrue(done.wait(3))
        finally:
            pub.close()

        self.assertEqual(
            received,
            [
                (
                    '/tmp/not-connected.sock',
                    'drive-activity',
                    None,
                )
            ],
        )

    def test_closed_publisher_rejects_new_events(self):
        pub = DriveActivityPublisher(
            '/tmp/not-connected.sock',
            sender=lambda *args: None,
        )
        pub.close()
        self.assertFalse(pub.enqueue())

    def test_bounded_queue_drops_excess_events(self):
        pub = DriveActivityPublisher(
            '/tmp/not-connected.sock',
            sender=lambda *args: None,
            max_pending=1,
        )
        self.assertTrue(pub.enqueue())
        self.assertFalse(pub.enqueue())
        pub.close()


class GPIOWakeMappingTests(unittest.TestCase):

    def test_normal_buttons_publish_activity(self):
        for name in ("stop", "skip", "previous"):
            with self.subTest(name=name):
                self.assertFalse(
                    should_publish_drive_activity(
                        name, "pressed", safe_executor=True
                    )
                )
                self.assertTrue(
                    should_publish_drive_activity(
                        name, "pressed", safe_executor=False
                    )
                )

    def test_encoder_movement_publishes_activity(self):
        for direction in ("left", "right"):
            with self.subTest(direction=direction):
                self.assertTrue(
                    should_publish_drive_activity(
                        "encoder", direction, safe_executor=True
                    )
                )

    def test_playpause_uses_own_wake_preflight(self):
        self.assertFalse(
            should_publish_drive_activity(
                "playpause", "pressed", safe_executor=True
            )
        )

    def test_releases_do_not_publish_activity(self):
        for name in ("playpause", "stop", "skip", "previous"):
            with self.subTest(name=name):
                self.assertFalse(
                    should_publish_drive_activity(
                        name, "released", safe_executor=True
                    )
                )

    def test_eject_uses_synchronous_wake(self):
        self.assertFalse(
            should_publish_drive_activity(
                "eject", "pressed", safe_executor=True
            )
        )
        self.assertTrue(
            should_publish_drive_activity(
                "eject", "pressed", safe_executor=False
            )
        )

    def test_volume_buttons_publish_async_wake_on_press_only(self):
        for name in ('volume_up', 'volume_dn'):
            for safe_executor in (False, True):
                self.assertTrue(should_publish_drive_activity(
                    name, 'pressed', safe_executor=safe_executor))
                self.assertFalse(should_publish_drive_activity(
                    name, 'released', safe_executor=safe_executor))

    def test_every_configured_gpio_button_is_covered(self):
        from pathlib import Path
        from sonoscdplayer.hardware.inputs.event_engine import load_config

        config = load_config(
            Path(__file__).resolve().parents[3]
            / "config/interface.example.toml"
        )

        names = set(config.buttons) | {"encoder_sw"}
        self.assertIn("volume_up", names)
        self.assertIn("volume_dn", names)

        for name in sorted(names):
            with self.subTest(button=name):
                expected = name not in {
                    "playpause", "stop", "skip",
                    "previous", "eject",
                }
                self.assertEqual(
                    should_publish_drive_activity(
                        name, "pressed", safe_executor=True
                    ),
                    expected,
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


if __name__ == '__main__':
    unittest.main()
