"""Command dispatch contract tests; all network calls are mocked."""
import contextlib
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.interfaces.interface_input import CommandDispatcher, handle_event, read_uid


class DispatchTests(unittest.TestCase):
    @patch('integrations.interfaces.interface_input.send_command')
    def test_passive_never_sends(self, send):
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('stop', 'pressed')
            handle_event('playpause', 'pressed', commands=True)
        send.assert_not_called()

    @patch('integrations.interfaces.interface_input.send_command')
    def test_execute_only_on_supported_press(self, send):
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('skip', 'pressed', commands=True, execute=True, uid='RINCON_TEST')
            handle_event('skip', 'released', commands=True, execute=True, uid='RINCON_TEST')
            handle_event('encoder', 'right', commands=True, execute=True, uid='RINCON_TEST')
        send.assert_called_once_with('/run/sonoscdplayer/control.sock', 'next', 'RINCON_TEST')

    @patch('integrations.interfaces.interface_input.send_command')
    def test_playpause_without_uid_skipped(self, send):
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('playpause', 'pressed', commands=True, execute=True)
        send.assert_not_called()

    def test_uid_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'uid'
            path.write_text('RINCON_TEST\n')
            path.chmod(0o600)
            self.assertEqual(read_uid(path), 'RINCON_TEST')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                read_uid(path)

    def test_nonblocking_busy_drops_events_and_never_retries(self):
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        calls = []

        def sender(path, command, uid):
            calls.append((path, command, uid))
            entered.set()
            release.wait(2)
            completed.set()
            raise TimeoutError('response unavailable')

        dispatcher = CommandDispatcher('/tmp/test.sock', sender=sender)
        with contextlib.redirect_stdout(io.StringIO()):
            started_at = time.monotonic()
            self.assertTrue(dispatcher.submit('stop'))
            self.assertLess(time.monotonic() - started_at, 0.5)
            self.assertTrue(entered.wait(1))
            self.assertFalse(dispatcher.submit('next'))
            self.assertEqual(len(calls), 1)
            release.set()
            self.assertTrue(completed.wait(1))
        self.assertEqual(calls, [('/tmp/test.sock', 'stop', None)])

    def test_uid_optional_for_non_playpause(self):
        calls = []
        done = threading.Event()

        def sender(path, command, uid):
            calls.append((command, uid))
            done.set()

        dispatcher = CommandDispatcher(sender=sender)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(dispatcher.submit('playpause'))
            self.assertTrue(dispatcher.submit('stop'))
        self.assertTrue(done.wait(1))
        self.assertEqual(calls, [('stop', None)])

    def test_event_dispatcher_path(self):
        calls = []
        class Stub:
            def submit(self, command):
                calls.append(command)
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('skip', 'pressed', commands=True, execute=True, dispatcher=Stub())
            handle_event('skip', 'released', commands=True, execute=True, dispatcher=Stub())
        self.assertEqual(calls, ['next'])


if __name__ == '__main__':
    unittest.main()
