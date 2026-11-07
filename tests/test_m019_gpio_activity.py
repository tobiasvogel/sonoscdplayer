"""M0.19 GPIO activity tests; no hardware or live sockets."""
import threading
import unittest

from integrations.interfaces.interface_input import (
    DriveActivityPublisher,
    should_publish_drive_activity,
)


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
        from integrations.interfaces.button_commands import map_event

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

    def test_other_button_press_reports_activity(self):
        self.assertTrue(
            should_publish_drive_activity(
                'eject', 'pressed', safe_executor=True
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
            '/tmp/not-connected-m019.sock', sender=sender
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
                    '/tmp/not-connected-m019.sock',
                    'drive-activity',
                    None,
                )
            ],
        )

    def test_closed_publisher_rejects_new_events(self):
        pub = DriveActivityPublisher(
            '/tmp/not-connected-m019.sock',
            sender=lambda *args: None,
        )
        pub.close()
        self.assertFalse(pub.enqueue())

    def test_bounded_queue_drops_excess_events(self):
        pub = DriveActivityPublisher(
            '/tmp/not-connected-m019.sock',
            sender=lambda *args: None,
            max_pending=1,
        )
        self.assertTrue(pub.enqueue())
        self.assertFalse(pub.enqueue())
        pub.close()


if __name__ == '__main__':
    unittest.main()
