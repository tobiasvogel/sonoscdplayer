"""M0.19 read-only OLED notice and asynchronous GPIO activity tests."""
import unittest
from unittest import mock
from threading import Event

from integrations.outputs.oled_status import (
    drive_waking, drive_wait_frame, status_frame, modern_status_frame)
from integrations.interfaces.interface_input import DriveActivityPublisher


class WaitNoticeTests(unittest.TestCase):
    def test_wake_flag_shows_identical_notice_in_both_ui_modes(self):
        state = {'player': {'mode': 'STOPPED'},
                 'drive_power': {'idle_waking': True}}
        expected = bytes(drive_wait_frame().data)
        self.assertTrue(drive_waking(state))
        self.assertEqual(bytes(status_frame(state).data), expected)
        self.assertEqual(bytes(modern_status_frame(state).data), expected)

    def test_notice_not_shown_for_spun_down_without_active_wake(self):
        self.assertFalse(drive_waking({'drive_power': {'idle_spun_down': True}}))
        self.assertFalse(drive_waking({}))
        self.assertFalse(drive_waking(None))


class ActivityPublisherTests(unittest.TestCase):
    def test_physical_activity_does_not_send_sonos_transport(self):
        sent, arrived = [], Event()
        def sender(socket_path, command, uid):
            sent.append((socket_path, command, uid))
            arrived.set()
        worker = DriveActivityPublisher('/fake.sock', sender=sender)
        worker.start()
        try:
            self.assertTrue(worker.enqueue())
            self.assertTrue(arrived.wait(2))
        finally:
            worker.close()
        self.assertEqual(sent, [('/fake.sock', 'drive-activity', None)])

    def test_closed_publisher_never_enqueues(self):
        worker = DriveActivityPublisher('/fake.sock', sender=lambda *args: None)
        worker.close()
        self.assertFalse(worker.enqueue())


if __name__ == '__main__':
    unittest.main()
