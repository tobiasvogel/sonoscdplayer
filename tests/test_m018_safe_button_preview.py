"""Offline checks for M0.18 opt-in read-only preview and GPIO routing."""
import contextlib
import io
import threading
import unittest

from integrations.interfaces.interface_input import handle_event
from integrations.interfaces.safe_button_preview import (
    SelectedTargetPreview, read_snapshot,
)


def state(mode='STOPPED', owned=False, active=False):
    return {'ok': True, 'state': {
        'schema_version': 1,
        'player': {'mode': mode},
        'sonos': {'owned': owned, 'group_cleanup_pending': False},
        'stream': {'active': active},
    }}


def selection(selected=True):
    return {'ok': True, 'target_selection': {'selected': selected}}


class PreviewTests(unittest.TestCase):
    def test_only_read_only_requests_and_no_transport_write(self):
        calls, messages, done = [], [], threading.Event()

        def reader(path, command):
            calls.append((path, command))
            return state() if command == 'status' else selection()

        def report(message):
            messages.append(message)
            done.set()

        worker = SelectedTargetPreview('/fake.sock', reader=reader, report=report)
        self.assertTrue(worker.submit())
        self.assertTrue(done.wait(2))
        self.assertEqual(calls, [('/fake.sock', 'status'), ('/fake.sock', 'get-target')])
        self.assertEqual(messages, ['selected-target preview: play-selected'])

    def test_no_selection_fails_closed(self):
        messages, done = [], threading.Event()

        def report(message):
            messages.append(message)
            done.set()

        worker = SelectedTargetPreview('/fake', reader=lambda path, cmd:
                                       state() if cmd == 'status' else selection(False),
                                       report=report)
        worker.submit()
        self.assertTrue(done.wait(2))
        self.assertEqual(messages, ['selected-target preview: blocked'])

    def test_read_failure_fails_closed_and_does_not_retry(self):
        calls, messages, done = [], [], threading.Event()

        def reader(path, cmd):
            calls.append(cmd)
            raise TimeoutError('unavailable')

        def report(message):
            messages.append(message)
            done.set()

        worker = SelectedTargetPreview('/fake', reader=reader, report=report)
        worker.submit()
        self.assertTrue(done.wait(2))
        self.assertEqual(calls, ['status'])
        self.assertEqual(messages, ['selected-target preview: unavailable'])

    def test_busy_press_is_dropped_not_queued(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def reader(path, cmd):
            calls.append(cmd)
            if cmd == 'status':
                entered.set()
                release.wait(2)
                return state()
            return selection()

        worker = SelectedTargetPreview('/fake', reader=reader,
                                       report=lambda msg: done.set())
        self.assertTrue(worker.submit())
        self.assertTrue(entered.wait(2))
        self.assertFalse(worker.submit())
        release.set()
        self.assertTrue(done.wait(2))
        self.assertEqual(calls, ['status', 'get-target'])

    def test_gpio_preview_routes_only_pressed_playpause(self):
        messages = []

        class Fake:
            def submit(self):
                messages.append('preview')
                return True

        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('playpause', 'pressed', commands=True, target_preview=Fake())
            handle_event('playpause', 'released', commands=True, target_preview=Fake())
            handle_event('stop', 'pressed', commands=True, target_preview=Fake())
            handle_event('playpause', 'pressed', commands=False, target_preview=Fake())
        self.assertEqual(messages, ['preview'])

    def test_preview_and_execute_are_separate_paths(self):
        class Fake:
            def submit(self):
                self.fail('preview cannot run during execution')

        sent = []

        class Dispatcher:
            def submit(self, cmd):
                sent.append(cmd)

        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('playpause', 'pressed', commands=True, execute=True,
                         uid='DUMMY_UID', dispatcher=Dispatcher())
        self.assertEqual(sent, ['playpause'])

    def test_reader_disallows_mutating_commands_before_socket(self):
        for command in ('play-selected', 'stop', 'pause', 'next', 'set-target'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                read_snapshot('/nonexistent', command)


if __name__ == '__main__':
    unittest.main()
