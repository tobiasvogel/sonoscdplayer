"""M0.19 read-only OLED notice and asynchronous GPIO activity tests."""
import unittest
from unittest import mock
from threading import Event
from integrations.interfaces.safe_button_execute import SelectedTargetExecutor

from integrations.outputs.oled_status import (
    drive_waking, drive_wait_frame, drive_fault, drive_fault_frame,
    status_frame, modern_status_frame)
from integrations.interfaces.interface_input import DriveActivityPublisher


class WaitNoticeTests(unittest.TestCase):
    def test_wake_flag_shows_identical_notice_in_both_ui_modes(self):
        state = {'player': {'mode': 'STOPPED'},
                 'drive_power': {'idle_waking': True}}
        expected = bytes(drive_wait_frame().data)
        self.assertTrue(drive_waking(state))
        self.assertEqual(bytes(status_frame(state).data), expected)
        self.assertEqual(bytes(modern_status_frame(state).data), expected)

    def test_failed_wake_displays_persistent_error_both_ui_modes(self):
        state = {'player': {'mode': 'STOPPED'},
                 'drive_power': {'idle_wake_fault_latched': True,
                                 'idle_spun_down': True,
                                 'idle_waking': False,
                                 'last_error': 'idle wake: Transport error'}}
        expected = bytes(drive_fault_frame().data)
        self.assertTrue(drive_fault(state))
        self.assertEqual(bytes(status_frame(state).data), expected)
        self.assertEqual(bytes(modern_status_frame(state).data), expected)
        state['drive_power']['idle_waking'] = True
        self.assertEqual(bytes(status_frame(state).data), expected)
        self.assertEqual(bytes(modern_status_frame(state).data), expected)

    def test_old_hardware_errors_do_not_trigger_new_fault_overlay(self):
        state = {'drive_power': {'idle_wake_fault_latched': False,
                                 'last_error': 'historical motor error'}}
        self.assertFalse(drive_fault(state))

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


class OrderedPlayWakeTests(unittest.TestCase):
    def test_play_waits_for_activity_ack_before_target_reads(self):
        steps, done = [], Event()

        def sender(path, command, uid):
            steps.append(command)
            return True

        def reader(path, command):
            steps.append(command)
            if command == 'status':
                return {'ok': True, 'state': {
                    'schema_version': 1, 'player': {'mode': 'STOPPED'},
                    'sonos': {'owned': False, 'group_cleanup_pending': False},
                    'stream': {'active': False}}}
            return {'ok': True, 'target_selection': {'selected': True}}

        worker = SelectedTargetExecutor(
            '/fake', reader=reader, sender=sender,
            activity_before_read=True, report=lambda message: done.set())
        self.assertTrue(worker.submit())
        self.assertTrue(done.wait(2))
        self.assertEqual(steps, ['drive-activity', 'status',
                                 'get-target', 'play-selected'])

    def test_uncertain_wake_blocks_all_target_reads_and_play(self):
        steps, done = [], Event()

        def sender(path, command, uid):
            steps.append(command)
            raise TimeoutError('unknown motor outcome')

        worker = SelectedTargetExecutor(
            '/fake', reader=lambda *args: steps.append('read'),
            sender=sender, activity_before_read=True,
            report=lambda message: done.set())
        self.assertTrue(worker.submit())
        self.assertTrue(done.wait(2))
        self.assertEqual(steps, ['drive-activity'])



if __name__ == '__main__':
    unittest.main()
