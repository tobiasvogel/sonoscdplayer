"""Button command mapping, dispatch and the command bridge to the player socket (mocked)."""
import contextlib
import io
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from sonoscdplayer.hardware.inputs.button_commands import map_event
from sonoscdplayer.hardware.inputs.button_service import (
    CommandDispatcher,
    handle_event,
    read_uid,
)
from sonoscdplayer.hardware.inputs.command_bridge import (
    Bridge,
    Command,
    map_event as bridge_map_event,
    send_command,
)
UID = 'RINCON_TEST_123'



class ButtonMappingTests(unittest.TestCase):
    def test_supported_press(self):
        self.assertEqual(map_event('previous', 'pressed'), 'previous')
        self.assertEqual(map_event('skip', 'pressed'), 'next')
        self.assertEqual(map_event('stop', 'pressed'), 'stop')
        self.assertEqual(map_event('playpause', 'pressed'), 'playpause')
        self.assertEqual(map_event('eject', 'pressed'), 'eject')

    def test_release_and_unknown_controls_are_inert(self):
        for name in ('previous', 'skip', 'stop', 'playpause', 'eject', 'encoder_sw',
                     'encoder', 'power', 'connect', 'volume_up', 'volume_dn'):
            for event in ('released', 'right', 'left'):
                self.assertIsNone(map_event(name, event))
        for name in ('encoder_sw', 'power', 'connect', 'volume_up', 'volume_dn'):
            self.assertIsNone(map_event(name, 'pressed'))


class DispatchTests(unittest.TestCase):
    @patch('sonoscdplayer.hardware.inputs.button_service.send_command')
    def test_passive_never_sends(self, send):
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('stop', 'pressed')
            handle_event('playpause', 'pressed', commands=True)
        send.assert_not_called()

    def test_execute_only_on_supported_press(self):
        calls = []
        class Stub:
            def submit(self, command):
                calls.append(command)
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('skip', 'pressed', commands=True, execute=True,
                         uid='RINCON_TEST', dispatcher=Stub())
            handle_event('skip', 'released', commands=True, execute=True,
                         uid='RINCON_TEST', dispatcher=Stub())
            handle_event('encoder', 'right', commands=True, execute=True,
                         uid='RINCON_TEST', dispatcher=Stub())
        self.assertEqual(calls, ['next'])

    @patch('sonoscdplayer.hardware.inputs.button_service.send_command')
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


class BridgeTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(bridge_map_event('playpause', 'pressed', UID), Command('playpause', UID))
        self.assertEqual(bridge_map_event('skip', 'pressed'), Command('next'))
        self.assertEqual(bridge_map_event('previous', 'pressed'), Command('previous'))
        self.assertEqual(bridge_map_event('stop', 'pressed'), Command('stop'))
    def test_release_ignored(self):
        self.assertIsNone(bridge_map_event('stop', 'released'))
        self.assertIsNone(bridge_map_event('encoder', 'right'))
        self.assertIsNone(bridge_map_event('power', 'pressed'))
    def test_play_requires_uid(self):
        with self.assertRaises(ValueError): bridge_map_event('playpause', 'pressed')
    def test_dry_run_never_sends(self):
        sender=MagicMock()
        b=Bridge(uid=UID, sender=sender)
        self.assertEqual(b.event('playpause','pressed')['command'],'playpause')
        sender.assert_not_called()
    def test_execute_exactly_once(self):
        sender=MagicMock(return_value={'ok':True})
        b=Bridge(uid=UID, sender=sender, dry_run=False)
        self.assertEqual(b.event('skip','pressed'),{'ok':True})
        self.assertEqual(sender.call_count, 1)
        sender.assert_called_once_with('/tmp/sonoscdplayer-control.sock', Command('next'))
    def test_no_force_flag(self):
        self.assertEqual(bridge_map_event('stop','pressed').payload(),{'command':'stop'})
    def test_sender_timeout_propagates_without_retry(self):
        with patch('sonoscdplayer.hardware.inputs.command_bridge.socket.socket') as factory:
            client=factory.return_value.__enter__.return_value
            client.recv.side_effect=TimeoutError('timed out')
            with self.assertRaises(TimeoutError):
                send_command('/tmp/nonexistent', Command('next'))
            self.assertEqual(client.sendall.call_count,1)
    def test_invalid_uid(self):
        with self.assertRaises(ValueError): Bridge(uid='bad uid')


if __name__ == '__main__':
    unittest.main()
