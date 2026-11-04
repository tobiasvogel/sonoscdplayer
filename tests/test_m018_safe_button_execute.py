"""M0.18 isolated execute-path tests; never connect to hardware or Sonos."""
import contextlib
import io
import threading
import unittest
from unittest import mock

from integrations.interfaces.interface_input import (handle_event, send_command)
from integrations.interfaces.safe_button_execute import SelectedTargetExecutor


def state(mode='STOPPED', owned=False, active=False, *, pending=False):
    return {'ok': True, 'state': {
        'schema_version': 1,
        'player': {'mode': mode},
        'sonos': {'owned': owned, 'group_cleanup_pending': pending},
        'stream': {'active': active},
    }}


def target(selected=True):
    return {'ok': True, 'target_selection': {'selected': selected}}


def run_one(reader, sender, action='playpause'):
    log, done = [], threading.Event()

    def report(message):
        log.append(message)
        done.set()

    worker = SelectedTargetExecutor('/not-a-socket', reader=reader,
                                    sender=sender, report=report)
    assert worker.submit(action)
    assert done.wait(2), 'executor did not finish'
    return log


class ExecuteTests(unittest.TestCase):
    def test_stopped_single_command_without_uid(self):
        reads, sends = [], []

        def reader(path, command):
            reads.append(command)
            return state() if command == 'status' else target()

        log = run_one(reader, lambda *args: sends.append(args))
        self.assertEqual(reads, ['status', 'get-target'])
        self.assertEqual(sends, [('/not-a-socket', 'play-selected', None)])
        self.assertIn('acknowledged', log[0])

    def test_pause_and_resume_under_ownership(self):
        for mode, expected in (('PLAYING', 'pause'), ('PAUSED', 'resume')):
            with self.subTest(mode=mode):
                sends = []
                run_one(lambda path, cmd: state(mode, True, True)
                        if cmd == 'status' else target(),
                        lambda *args: sends.append(args))
                self.assertEqual(sends, [('/not-a-socket', expected, None)])

    def test_unselected_stopped_blocks(self):
        sends = []
        log = run_one(lambda path, cmd: state() if cmd == 'status'
                      else target(False), lambda *args: sends.append(args))
        self.assertEqual(sends, [])
        self.assertEqual(log, ['selected-target execute: blocked'])

    def test_ambiguous_group_blocks(self):
        sends = []
        log = run_one(lambda path, cmd: state(pending=True) if cmd == 'status'
                      else target(), lambda *args: sends.append(args))
        self.assertEqual(sends, [])
        self.assertEqual(log, ['selected-target execute: blocked'])

    def test_malformed_and_failed_reads_never_send(self):
        for invalid in (None, {}, {'ok': False}, {'ok': True}):
            with self.subTest(invalid=invalid):
                sends = []
                run_one(lambda path, cmd: invalid,
                        lambda *args: sends.append(args))
                self.assertEqual(sends, [])

    def test_read_timeout_no_retry_or_send(self):
        calls, sends = [], []

        def reader(path, command):
            calls.append(command)
            raise TimeoutError('simulated')

        log = run_one(reader, lambda *args: sends.append(args))
        self.assertEqual(calls, ['status'])
        self.assertEqual(sends, [])
        self.assertIn('not retrying', log[0])

    def test_rejected_send_never_retries(self):
        sends = []

        def sender(*args):
            sends.append(args)
            raise TimeoutError('unknown acceptance')

        log = run_one(lambda path, cmd: state() if cmd == 'status' else target(),
                      sender)
        self.assertEqual(sends, [('/not-a-socket', 'play-selected', None)])
        self.assertIn('not retrying', log[0])

    def test_busy_button_dropped(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        sends = []

        def reader(path, cmd):
            if cmd == 'status':
                entered.set()
                release.wait(2)
                return state()
            return target()

        worker = SelectedTargetExecutor('/fake', reader=reader,
                                        sender=lambda *args: sends.append(args),
                                        report=lambda message: done.set())
        self.assertTrue(worker.submit())
        self.assertTrue(entered.wait(2))
        self.assertFalse(worker.submit())
        release.set()
        self.assertTrue(done.wait(2))
        self.assertEqual(len(sends), 1)

    def test_gpio_safe_path_supersedes_legacy_playpause(self):
        safe, legacy = [], []

        class Safe:
            def submit(self, action='playpause'):
                safe.append(action)

        class Legacy:
            def submit(self, cmd):
                legacy.append(cmd)

        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('playpause', 'pressed', commands=True, execute=True,
                         target_executor=Safe(), dispatcher=Legacy())
            handle_event('playpause', 'released', commands=True, execute=True,
                         target_executor=Safe(), dispatcher=Legacy())
            handle_event('stop', 'pressed', commands=True, execute=True,
                         target_executor=Safe(), dispatcher=Legacy())
            handle_event('skip', 'pressed', commands=True, execute=True,
                         target_executor=Safe(), dispatcher=Legacy())
        self.assertEqual(safe, ['playpause', 'stop'])
        self.assertEqual(legacy, [])

    def test_stop_one_shot_with_no_uid(self):
        sends = []
        run_one(lambda path, cmd: state('PLAYING', True, True)
                if cmd == 'status' else target(),
                lambda *args: sends.append(args), action='stop')
        self.assertEqual(sends, [('/not-a-socket', 'stop', None)])

    def test_stop_blocks_when_unowned_or_cleanup_pending(self):
        for snap in (state('PLAYING', False, True),
                     state('PLAYING', True, True, pending=True),
                     state('STOPPED', False, False)):
            with self.subTest(snap=snap):
                sends = []
                run_one(lambda path, cmd: snap if cmd == 'status' else target(),
                        lambda *args: sends.append(args), action='stop')
                self.assertEqual(sends, [])

    def test_stop_sender_timeout_does_not_retry(self):
        sends = []
        def sender(*args):
            sends.append(args)
            raise TimeoutError('acceptance unknown')
        log = run_one(lambda path, cmd: state('PAUSED', True, True)
                      if cmd == 'status' else target(),
                      sender, action='stop')
        self.assertEqual(sends, [('/not-a-socket', 'stop', None)])
        self.assertIn('not retrying', log[0])

    def test_unknown_action_never_dispatches(self):
        sends = []
        worker = SelectedTargetExecutor('/fake',
                  reader=lambda *args: (_ for _ in ()).throw(AssertionError()),
                  sender=lambda *args: sends.append(args))
        self.assertFalse(worker.submit('next'))
        self.assertEqual(sends, [])


class TransportAcknowledgementTests(unittest.TestCase):
    """No Sonos/GPIO: inspect real sender's socket timeouts and one-shot I/O."""

    class Socket:
        def __init__(self, reply=b'{"ok":true}\\n', fail_recv=False):
            self.reply = reply
            self.fail_recv = fail_recv
            self.timeouts = []
            self.connections = []
            self.requests = []
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def settimeout(self, seconds):
            self.timeouts.append(seconds)

        def connect(self, path):
            self.connections.append(path)

        def sendall(self, data):
            self.requests.append(data)

        def recv(self, size):
            self.reads += 1
            if self.fail_recv:
                raise TimeoutError('simulated unknown acceptance')
            return self.reply

    def test_command_specific_bounded_acknowledgement_deadlines(self):
        for command, lower, upper in (
            ('play-selected', 129.0, 130.0),
            ('pause', 9.0, 10.0),
            ('resume', 9.0, 10.0),
            ('stop', 9.0, 10.0),
            ('help', 0.5, 1.0),
        ):
            with self.subTest(command=command):
                client = self.Socket()
                with mock.patch('integrations.interfaces.interface_input.socket.socket',
                                return_value=client):
                    self.assertTrue(send_command('/fake', command))
                self.assertEqual(client.connections, ['/fake'])
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(client.reads, 1)
                self.assertEqual(client.timeouts[0], 1.0)
                self.assertGreaterEqual(client.timeouts[-1], lower)
                self.assertLessEqual(client.timeouts[-1], upper)

    def test_timeout_never_reconnects_or_resends(self):
        client = self.Socket(fail_recv=True)
        with mock.patch('integrations.interfaces.interface_input.socket.socket',
                        return_value=client):
            with self.assertRaises(TimeoutError):
                send_command('/fake', 'play-selected')
        self.assertEqual(client.connections, ['/fake'])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.reads, 1)

    def test_total_response_deadline_expires_without_retry(self):
        client = self.Socket()
        with mock.patch('integrations.interfaces.interface_input.socket.socket',
                        return_value=client), mock.patch(
                        'integrations.interfaces.interface_input.time.monotonic',
                        side_effect=[100.0, 111.0]):
            with self.assertRaisesRegex(TimeoutError, 'deadline exceeded'):
                send_command('/fake', 'stop')
        self.assertEqual(client.connections, ['/fake'])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.reads, 0)


if __name__ == '__main__':
    unittest.main()
