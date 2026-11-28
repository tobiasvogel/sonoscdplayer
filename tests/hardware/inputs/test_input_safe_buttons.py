"""Safe physical transport buttons: policy, preview and execute paths for PLAY/STOP/NEXT/PREVIOUS/EJECT (offline)."""
import contextlib
import io
import threading
import unittest
from unittest import mock
from unittest.mock import Mock, patch
from sonoscdplayer.hardware.inputs.play_button_policy import (
    plan_playpause,
    plan_stop,
    plan_track_change,
    plan_eject,
)
from sonoscdplayer.hardware.inputs.button_service import (
    handle_event,
    send_command,
    TRANSPORT_RESPONSE_TIMEOUTS,
)
from sonoscdplayer.hardware.inputs.play_button_preview import (
    SelectedTargetPreview,
    read_snapshot,
)
from sonoscdplayer.hardware.inputs.play_button_execute import SelectedTargetExecutor
from sonoscdplayer.drive.disc_state import DiscMode, DiscStatus
from sonoscdplayer.player.state import Mode, PlayerState
from sonoscdplayer.drive.eject import CDROM_LOCKDOOR, CDROMEJECT, eject_tray, eject_verified_idle
from sonoscdplayer.player.service import handle_command
from sonoscdplayer.hardware.inputs.button_commands import map_event
DEVICE = '/dev/sr9'
IDENTITY = object()
TOC = object()



def snapshot(mode='STOPPED', *, owned=False, active=False, pending=False):
    return {
        'ok': True,
        'state': {
            'schema_version': 1,
            'player': {'mode': mode},
            'sonos': {'owned': owned, 'group_cleanup_pending': pending},
            'stream': {'active': active},
        },
    }


def target(selected=True):
    return {'ok': True, 'target_selection': {'selected': selected}}


class SafeButtonPolicyTests(unittest.TestCase):
    def test_stopped_requires_player_acknowledged_target(self):
        self.assertEqual(plan_playpause(snapshot(), target()), 'play-selected')
        self.assertIsNone(plan_playpause(snapshot(), target(False)))

    def test_pause_and_resume_require_owned_active_stream(self):
        self.assertEqual(plan_playpause(
            snapshot('PLAYING', owned=True, active=True), target(False)), 'pause')
        self.assertEqual(plan_playpause(
            snapshot('PAUSED', owned=True, active=True), target(False)), 'resume')
        for mode in ('PLAYING', 'PAUSED'):
            for owned, active in ((False, True), (True, False), (False, False)):
                with self.subTest(mode=mode, owned=owned, active=active):
                    self.assertIsNone(plan_playpause(
                        snapshot(mode, owned=owned, active=active), target()))

    def test_pending_cleanup_blocks_pause_and_resume(self):
        for mode, owned, active in (('PLAYING', True, True), ('PAUSED', True, True)):
            self.assertIsNone(plan_playpause(
                snapshot(mode, owned=owned, active=active, pending=True), target()))

    def test_pending_cleanup_lets_the_player_decide_on_play(self):
        # The player releases an idle group of its own speakers before PLAY.
        self.assertEqual(plan_playpause(snapshot('STOPPED', pending=True), target()),
                         'play-selected')

    def test_transitional_error_and_no_disc_modes_fail_closed(self):
        for mode in ('STARTING', 'NO_DISC', 'UNKNOWN'):
            with self.subTest(mode=mode):
                self.assertIsNone(plan_playpause(snapshot(mode), target()))

    def test_released_error_play_retries_and_stop_clears(self):
        """a player error must be resolvable at the device."""
        self.assertEqual(plan_playpause(snapshot('ERROR'), target()), 'play-selected')
        self.assertEqual(plan_stop(snapshot('ERROR'), target()), 'reset')
        self.assertEqual(plan_stop(snapshot('ERROR'), target(False)), 'reset')
        for owned, active, pending in ((True, False, False), (False, True, False)):
            with self.subTest(owned=owned, active=active, pending=pending):
                still = snapshot('ERROR', owned=owned, active=active, pending=pending)
                self.assertIsNone(plan_playpause(still, target()))
                self.assertIsNone(plan_stop(still, target()))
        self.assertIsNone(plan_playpause(snapshot('ERROR'), target(False)))

    def test_rejected_or_malformed_status_never_authorizes(self):
        invalid = [None, [], {}, {'ok': False}, {'ok': True},
                   {'ok': True, 'state': {'schema_version': 1}}]
        for item in invalid:
            with self.subTest(status=item):
                self.assertIsNone(plan_playpause(item, target()))
        for item in (None, [], {}, {'ok': False}, {'ok': True}):
            with self.subTest(target=item):
                self.assertIsNone(plan_playpause(snapshot(), item))
        broken = snapshot()
        broken['state']['schema_version'] = 2
        self.assertIsNone(plan_playpause(broken, target()))

    def test_unknown_ownership_or_stream_flags_fail_closed(self):
        for field, value in (('owned', None), ('owned', 1),
                             ('active', None), ('active', 1),
                             ('group_cleanup_pending', None)):
            snap = snapshot()
            container = 'stream' if field == 'active' else 'sonos'
            snap['state'][container][field] = value
            with self.subTest(field=field, value=value):
                self.assertIsNone(plan_playpause(snap, target()))

    def test_selection_flag_must_be_exact_true(self):
        for value in (None, 1, 'true', False):
            self.assertIsNone(plan_playpause(
                snapshot(), {'ok': True, 'target_selection': {'selected': value}}))

    def test_stop_only_for_owned_active_selected_playback(self):
        for mode in ('PLAYING', 'PAUSED'):
            self.assertEqual(plan_stop(
                snapshot(mode, owned=True, active=True), target()), 'stop')
        # Stopped with nothing of ours on Sonos: STOP only selects track 1.
        for selected in (True, False):
            self.assertEqual(plan_stop(
                snapshot('STOPPED', owned=False, active=False), target(selected)), 'stop')
        for mode, owned, active, pending, selected in (
                ('STOPPED', False, False, True, True),
                ('STOPPED', True, False, False, True),
                ('STOPPED', False, True, False, True),
                ('PLAYING', False, True, False, True),
                ('PAUSED', True, False, False, True),
                ('PLAYING', True, True, True, True),
                ('PLAYING', True, True, False, False),
                ('ERROR', True, True, False, True)):
            self.assertIsNone(plan_stop(
                snapshot(mode, owned=owned, active=active, pending=pending),
                target(selected)))

    def test_stop_fail_closed_on_bad_snapshots(self):
        for item in (None, {}, {'ok': False}, {'ok': True}):
            self.assertIsNone(plan_stop(item, target()))
            self.assertIsNone(plan_stop(snapshot('PLAYING', owned=True, active=True), item))


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


def status_reply(mode='STOPPED', owned=False, active=False, *, pending=False):
    return {'ok': True, 'state': {
        'schema_version': 1,
        'player': {'mode': mode},
        'sonos': {'owned': owned, 'group_cleanup_pending': pending},
        'stream': {'active': active},
    }}


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
            return status_reply() if command == 'status' else target()

        log = run_one(reader, lambda *args: sends.append(args))
        self.assertEqual(reads, ['status', 'get-target'])
        self.assertEqual(sends, [('/not-a-socket', 'play-selected', None)])
        self.assertIn('acknowledged', log[0])

    def test_pause_and_resume_under_ownership(self):
        for mode, expected in (('PLAYING', 'pause'), ('PAUSED', 'resume')):
            with self.subTest(mode=mode):
                sends = []
                run_one(lambda path, cmd: status_reply(mode, True, True)
                        if cmd == 'status' else target(),
                        lambda *args: sends.append(args))
                self.assertEqual(sends, [('/not-a-socket', expected, None)])

    def test_unselected_stopped_blocks(self):
        sends = []
        log = run_one(lambda path, cmd: status_reply() if cmd == 'status'
                      else target(False), lambda *args: sends.append(args))
        self.assertEqual(sends, [])
        self.assertEqual(log, ['selected-target execute: blocked'])

    def test_pending_group_is_left_to_the_player(self):
        sends = []
        run_one(lambda path, cmd: status_reply(pending=True) if cmd == 'status'
                else target(), lambda *args: sends.append(args))
        self.assertEqual([args[1] for args in sends], ['play-selected'])

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

        log = run_one(lambda path, cmd: status_reply() if cmd == 'status' else target(),
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
                return status_reply()
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
        self.assertEqual(safe, ['playpause', 'stop', 'next'])
        self.assertEqual(legacy, [])

    def test_stop_sends_reset_for_released_error(self):
        sends = []
        log = run_one(lambda path, command: status_reply('ERROR') if command == 'status'
                      else target(), lambda *args: sends.append(args), action='stop')
        self.assertEqual(sends, [('/not-a-socket', 'reset', None)])
        self.assertEqual(log, ['selected-target execute: reset acknowledged'])

    def test_stop_one_shot_with_no_uid(self):
        sends = []
        run_one(lambda path, cmd: status_reply('PLAYING', True, True)
                if cmd == 'status' else target(),
                lambda *args: sends.append(args), action='stop')
        self.assertEqual(sends, [('/not-a-socket', 'stop', None)])

    def test_stop_blocks_when_unowned_or_cleanup_pending(self):
        for snap in (status_reply('PLAYING', False, True),
                     status_reply('PLAYING', True, True, pending=True),
                     status_reply('STOPPED', False, False, pending=True)):
            with self.subTest(snap=snap):
                sends = []
                run_one(lambda path, cmd: snap if cmd == 'status' else target(),
                        lambda *args: sends.append(args), action='stop')
                self.assertEqual(sends, [])

    def test_stop_while_stopped_goes_back_to_track_one(self):
        sends = []
        run_one(lambda path, cmd: status_reply('STOPPED', False, False)
                if cmd == 'status' else target(False),
                lambda *args: sends.append(args), action='stop')
        self.assertEqual(sends, [('/not-a-socket', 'stop', None)])

    def test_stop_sender_timeout_does_not_retry(self):
        sends = []
        def sender(*args):
            sends.append(args)
            raise TimeoutError('acceptance unknown')
        log = run_one(lambda path, cmd: status_reply('PAUSED', True, True)
                      if cmd == 'status' else target(),
                      sender, action='stop')
        self.assertEqual(sends, [('/not-a-socket', 'stop', None)])
        self.assertIn('not retrying', log[0])

    def test_foreign_playback_cannot_pause_resume_or_stop(self):
        # An active stream without local ownership must never authorize writes.
        for mode, action in (('PLAYING', 'playpause'),
                             ('PAUSED', 'playpause'),
                             ('PLAYING', 'stop'),
                             ('PAUSED', 'stop')):
            with self.subTest(mode=mode, action=action):
                calls, sends = [], []

                def reader(path, command):
                    calls.append(command)
                    return (status_reply(mode, False, True) if command == 'status'
                            else target())

                log = run_one(reader, lambda *args: sends.append(args),
                              action=action)
                self.assertEqual(calls, ['status', 'get-target'])
                self.assertEqual(sends, [])
                self.assertEqual(log, ['selected-target execute: blocked'])

    def test_missing_selection_blocks_play_and_owned_stop(self):
        for mode, owned, active, action in (
            ('STOPPED', False, False, 'playpause'),
            ('PLAYING', True, True, 'stop'),
        ):
            with self.subTest(mode=mode, action=action):
                sends = []
                log = run_one(lambda path, command:
                              status_reply(mode, owned, active) if command == 'status'
                              else target(False),
                              lambda *args: sends.append(args), action=action)
                self.assertEqual(sends, [])
                self.assertEqual(log, ['selected-target execute: blocked'])

    def test_ambiguous_states_block_all_hardware_transport(self):
        for mode, owned, active, pending in (
            ('STARTING', False, False, False),
            ('ERROR', True, True, False),
            ('PLAYING', True, True, True),
            ('PAUSED', True, True, True),
        ):
            for action in ('playpause', 'stop'):
                with self.subTest(mode=mode, action=action):
                    sends = []
                    log = run_one(lambda path, command:
                                  status_reply(mode, owned, active, pending=pending)
                                  if command == 'status' else target(),
                                  lambda *args: sends.append(args),
                                  action=action)
                    self.assertEqual(sends, [])
                    self.assertEqual(log, ['selected-target execute: blocked'])

    def test_rejected_target_snapshot_cannot_send_stop(self):
        for invalid in (None, {}, {'ok': False}, {'ok': True},
                        {'ok': True, 'target_selection': {'selected': None}}):
            with self.subTest(invalid=invalid):
                sends = []
                run_one(lambda path, command:
                        status_reply('PLAYING', True, True) if command == 'status'
                        else invalid,
                        lambda *args: sends.append(args), action='stop')
                self.assertEqual(sends, [])

    def test_unknown_action_never_dispatches(self):
        sends = []
        worker = SelectedTargetExecutor('/fake',
                  reader=lambda *args: (_ for _ in ()).throw(AssertionError()),
                  sender=lambda *args: sends.append(args))
        self.assertFalse(worker.submit('power'))
        self.assertEqual(sends, [])


class TransportAcknowledgementTests(unittest.TestCase):
    """No Sonos/GPIO: inspect real sender's socket timeouts and one-shot I/O."""

    class Socket:
        def __init__(self, reply=b'{"ok":true}\n', fail_recv=False):
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
                with mock.patch('sonoscdplayer.hardware.inputs.button_service.socket.socket',
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
        with mock.patch('sonoscdplayer.hardware.inputs.button_service.socket.socket',
                        return_value=client):
            with self.assertRaises(TimeoutError):
                send_command('/fake', 'play-selected')
        self.assertEqual(client.connections, ['/fake'])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.reads, 1)

    def test_total_response_deadline_expires_without_retry(self):
        client = self.Socket()
        with mock.patch('sonoscdplayer.hardware.inputs.button_service.socket.socket',
                        return_value=client), mock.patch(
                        'sonoscdplayer.hardware.inputs.button_service.time.monotonic',
                        side_effect=[100.0, 111.0]):
            with self.assertRaisesRegex(TimeoutError, 'deadline exceeded'):
                send_command('/fake', 'stop')
        self.assertEqual(client.connections, ['/fake'])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.reads, 0)


def status(mode='PLAYING', owned=True, active=True, pending=False):
    return {'ok': True, 'state': {
        'schema_version': 1,
        'player': {'mode': mode},
        'sonos': {'owned': owned, 'group_cleanup_pending': pending},
        'stream': {'active': active},
    }}


class TrackPolicyTests(unittest.TestCase):
    def test_allowed_states(self):
        for direction in ('next', 'previous'):
            with self.subTest(direction=direction):
                self.assertEqual(plan_track_change(status(), target(), direction), direction)
                self.assertEqual(plan_track_change(
                    status('PAUSED'), target(), direction), direction)
                self.assertEqual(plan_track_change(
                    status('STOPPED', False, False), target(), direction), direction)

    def test_fail_closed_conditions(self):
        scenarios = [
            (status('STOPPED', True, False), target()),
            (status('STOPPED', False, True), target()),
            (status('PLAYING', False, True), target()),
            (status('PAUSED', True, False), target()),
            (status('STARTING', False, False), target()),
            (status('ERROR', True, True), target()),
            (status(pending=True), target()),
            (status(), target(False)),
            (status(), {'ok': True, 'target_selection': {'selected': 1}}),
            (None, target()),
            (status(), None),
            ({'ok': False}, target()),
            (status(), {'ok': False}),
        ]
        for snap, selection in scenarios:
            for direction in ('next', 'previous'):
                with self.subTest(snap=snap, selection=selection, direction=direction):
                    self.assertIsNone(plan_track_change(snap, selection, direction))

    def test_malformed_and_unknown_rejected(self):
        for direction in ('next', 'previous', 'skip', None, ''):
            snap = status()
            snap['state']['schema_version'] = 2
            self.assertIsNone(plan_track_change(snap, target(), direction))
        self.assertIsNone(plan_track_change(status(), target(), 'skip'))
        for field, value in (('owned', 1), ('active', None),
                             ('group_cleanup_pending', None)):
            snap = status()
            part = 'stream' if field == 'active' else 'sonos'
            snap['state'][part][field] = value
            self.assertIsNone(plan_track_change(snap, target(), 'next'))


class TrackExecuteTests(unittest.TestCase):
    def run_once(self, action, current=None, selection=None, sender=None):
        calls, sends, reports = [], [], []
        done = threading.Event()

        def reader(path, command):
            calls.append(command)
            return (current if current is not None else status()) if command == 'status' else (
                selection if selection is not None else target())

        def send(*args):
            sends.append(args)
            if sender is not None:
                sender(*args)

        def report(message):
            reports.append(message)
            done.set()

        executor = SelectedTargetExecutor('/offline', reader=reader,
                                          sender=send, report=report)
        self.assertTrue(executor.submit(action))
        self.assertTrue(done.wait(2))
        return calls, sends, reports

    def test_only_authorized_command_is_sent(self):
        for action in ('next', 'previous'):
            calls, sends, reports = self.run_once(action)
            self.assertEqual(calls, ['status', 'get-target'])
            self.assertEqual(sends, [('/offline', action, None)])
            self.assertEqual(reports, [
                'selected-target execute: ' + action + ' acknowledged'])

    def test_stopped_selection_changes_without_ownership(self):
        _, sends, _ = self.run_once('next',
                                   current=status('STOPPED', False, False))
        self.assertEqual(sends, [('/offline', 'next', None)])

    def test_foreign_and_uncertain_playback_never_send(self):
        for snap in (status('PLAYING', False, True),
                     status('PAUSED', True, False),
                     status(pending=True),
                     status('STARTING', False, False)):
            _, sends, reports = self.run_once('previous', current=snap)
            self.assertEqual(sends, [])
            self.assertEqual(reports, ['selected-target execute: blocked'])

    def test_read_failure_and_ambiguous_send_no_retry(self):
        sends = []
        def timeout(*args):
            sends.append(args)
            raise TimeoutError('ambiguous acceptance')
        _, issued, reports = self.run_once('next', sender=timeout)
        self.assertEqual(issued, [('/offline', 'next', None)])
        self.assertEqual(sends, issued)
        self.assertIn('not retrying', reports[0])

    def busy_executor(self, mode_after='STOPPED'):
        """An executor whose first status read waits until released.

        Returns (executor, entered, release, sends).
        """
        entered, release = threading.Event(), threading.Event()
        sends, reads = [], []
        def reader(path, command):
            if command == 'status':
                reads.append(command)
                if len(reads) == 1:
                    entered.set()
                    release.wait(2)
                    return status('STOPPED', False, False)
                return status(mode_after, mode_after != 'STOPPED', mode_after != 'STOPPED')
            return target()
        executor = SelectedTargetExecutor('/offline', reader=reader,
                   sender=lambda *args: sends.append(args), report=lambda _: None)
        return executor, entered, release, sends

    def wait_idle(self, executor):
        for _ in range(200):
            with executor._lock:
                if not executor._busy:
                    return
            threading.Event().wait(0.01)
        self.fail('executor did not become idle')

    def test_presses_while_busy_are_counted_and_sent_while_stopped(self):
        executor, entered, release, sends = self.busy_executor()
        self.assertTrue(executor.submit('next'))
        self.assertTrue(entered.wait(2))
        for _ in range(4):
            self.assertTrue(executor.submit('next'))
        self.assertTrue(executor.submit('previous'))
        release.set()
        self.wait_idle(executor)
        # 1 press, then the net +3 of the presses made meanwhile, one by one.
        self.assertEqual(sends, [('/offline', 'next', None)] * 4)

    def test_presses_while_busy_add_up_with_direction(self):
        for extra, expected in ((['previous', 'previous'], ['next', 'previous', 'previous']),
                                (['next', 'previous'], ['next'])):
            with self.subTest(extra=extra):
                executor, entered, release, sends = self.busy_executor()
                self.assertTrue(executor.submit('next'))
                self.assertTrue(entered.wait(2))
                for action in extra:
                    self.assertTrue(executor.submit(action))
                release.set()
                self.wait_idle(executor)
                self.assertEqual(sends, [('/offline', a, None) for a in expected])

    def test_counted_presses_dropped_once_playback_started(self):
        # Playback behaviour is unchanged: no queued jumps on Sonos.
        executor, entered, release, sends = self.busy_executor(mode_after='PLAYING')
        self.assertTrue(executor.submit('next'))
        self.assertTrue(entered.wait(2))
        self.assertTrue(executor.submit('next'))
        release.set()
        self.wait_idle(executor)
        self.assertEqual(sends, [('/offline', 'next', None)])

    def test_other_busy_presses_still_dropped(self):
        executor, entered, release, sends = self.busy_executor()
        self.assertTrue(executor.submit('next'))
        self.assertTrue(entered.wait(2))
        for action in ('playpause', 'stop', 'eject'):
            self.assertFalse(executor.submit(action))
        release.set()
        self.wait_idle(executor)
        self.assertEqual(sends, [('/offline', 'next', None)])

    def test_gpio_events_and_release_mapping(self):
        submitted = []
        class FakeExecutor:
            def submit(self, action='playpause'):
                submitted.append(action)
                return True
        with contextlib.redirect_stdout(io.StringIO()):
            for button in ('skip', 'previous'):
                handle_event(button, 'pressed', commands=True, execute=True,
                             target_executor=FakeExecutor())
                handle_event(button, 'released', commands=True, execute=True,
                             target_executor=FakeExecutor())
        self.assertEqual(submitted, ['next', 'previous'])

    def test_track_command_ack_deadline(self):
        self.assertEqual(TRANSPORT_RESPONSE_TIMEOUTS['next'], 130.0)
        self.assertEqual(TRANSPORT_RESPONSE_TIMEOUTS['previous'], 130.0)


def eject_snapshot(mode='STOPPED', owned=False, active=False, pending=False, disc='READY'):
    return {'ok': True, 'state': {
        'schema_version': 1,
        'player': {'mode': mode},
        'sonos': {'owned': owned, 'group_cleanup_pending': pending},
        'stream': {'active': active},
        'disc': {'mode': disc, 'device': DEVICE},
    }}


class FakeServer:
    device = DEVICE
    running = False
    reading = False

    def alive(self):
        return self.running

    def reader_active(self):
        return self.reading


class FakeController:
    def __init__(self):
        self.state = PlayerState(Mode.STOPPED, (1, 2), 1)
        self.owned_url = None
        self.player = None
        self._joined = {}
        self._group_uncertain = False
        self.server = FakeServer()
        self.disc_status = DiscStatus.ready(DEVICE, 2)
        self.display_toc = TOC

    def status(self):
        return 'fake status'

    def status_snapshot(self):
        return eject_snapshot()['state']


class FakeMonitor:
    device = DEVICE
    identity = IDENTITY
    loaded_toc = TOC
    _disconnected = False


@contextlib.contextmanager
def fake_lock(device, *, blocking=False):
    assert device == DEVICE
    assert blocking is False
    yield True


class EjectIoctlTests(unittest.TestCase):
    def test_unlock_then_single_eject_on_same_descriptor(self):
        with patch('sonoscdplayer.drive.eject.os.open', return_value=17) as op, \
                patch('sonoscdplayer.drive.eject.fcntl.ioctl') as ioctl, \
                patch('sonoscdplayer.drive.eject.os.close') as close:
            eject_tray(DEVICE)
        self.assertEqual(op.call_count, 1)
        self.assertEqual(ioctl.call_args_list, [
            unittest.mock.call(17, CDROM_LOCKDOOR, 0),
            unittest.mock.call(17, CDROMEJECT),
        ])
        close.assert_called_once_with(17)

    def test_unlock_rejection_prevents_eject_and_closes_descriptor(self):
        with patch('sonoscdplayer.drive.eject.os.open', return_value=17), \
                patch('sonoscdplayer.drive.eject.fcntl.ioctl',
                      side_effect=OSError('unlock rejected')) as ioctl, \
                patch('sonoscdplayer.drive.eject.os.close') as close:
            with self.assertRaisesRegex(OSError, 'unlock rejected'):
                eject_tray(DEVICE)
        ioctl.assert_called_once_with(17, CDROM_LOCKDOOR, 0)
        close.assert_called_once_with(17)

    def test_eject_rejection_closes_descriptor_without_retry(self):
        with patch('sonoscdplayer.drive.eject.os.open', return_value=17), \
                patch('sonoscdplayer.drive.eject.fcntl.ioctl',
                      side_effect=[0, OSError('eject rejected')]) as ioctl, \
                patch('sonoscdplayer.drive.eject.os.close') as close:
            with self.assertRaisesRegex(OSError, 'eject rejected'):
                eject_tray(DEVICE)
        self.assertEqual(ioctl.call_count, 2)
        close.assert_called_once_with(17)


class EjectPolicyTests(unittest.TestCase):
    def test_idle_ready_permitted_without_target(self):
        self.assertEqual(plan_eject(eject_snapshot()), 'eject')

    def test_fail_closed(self):
        bad = [
            eject_snapshot(mode='PLAYING', owned=True, active=True),
            eject_snapshot(mode='PAUSED', owned=True, active=True),
            eject_snapshot(mode='STARTING'),
            eject_snapshot(mode='NO_DISC', disc='NO_DISC'),
            eject_snapshot(owned=True),
            eject_snapshot(active=True),
            eject_snapshot(disc='EJECTED'),
            {'ok': False},
            None,
        ]
        for item in bad:
            with self.subTest(item=item):
                self.assertIsNone(plan_eject(item))
        s = eject_snapshot()
        s['state']['schema_version'] = 2
        self.assertIsNone(plan_eject(s))
        # A pending group cleanup is decided by the player (it releases an
        # idle group of its own speakers first).
        self.assertEqual(plan_eject(eject_snapshot(pending=True)), 'eject')
        s = eject_snapshot()
        s['state']['sonos']['owned'] = 0
        self.assertIsNone(plan_eject(s))

    def test_gpio_press_only_one_action(self):
        self.assertEqual(map_event('eject', 'pressed'), 'eject')
        self.assertIsNone(map_event('eject', 'released'))
        self.assertEqual(TRANSPORT_RESPONSE_TIMEOUTS['eject'], 10.0)
        actions = []
        class Executor:
            def submit(self, action='playpause'):
                actions.append(action)
                return True
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('eject', 'pressed', commands=True, execute=True,
                         target_executor=Executor())
            handle_event('eject', 'released', commands=True, execute=True,
                         target_executor=Executor())
        self.assertEqual(actions, ['eject'])

    def test_execute_reads_only_status_and_sends_once(self):
        calls, sent, reports = [], [], []
        done = threading.Event()

        def reader(path, cmd):
            calls.append(cmd)
            return eject_snapshot()

        def report(msg):
            reports.append(msg)
            done.set()

        executor = SelectedTargetExecutor('/offline', reader=reader,
                                          sender=lambda *args: sent.append(args),
                                          report=report)
        self.assertTrue(executor.submit('eject'))
        self.assertTrue(done.wait(2))
        self.assertEqual(calls, ['status'])
        self.assertEqual(sent, [('/offline', 'eject', None)])
        self.assertEqual(reports, ['selected-target execute: eject acknowledged'])

    def test_execute_rejection_never_sends(self):
        sent, reports = [], []
        done = threading.Event()
        executor = SelectedTargetExecutor(
            '/offline', reader=lambda *_: eject_snapshot(mode='PLAYING'),
            sender=lambda *args: sent.append(args),
            report=lambda msg: (reports.append(msg), done.set()))
        self.assertTrue(executor.submit('eject'))
        self.assertTrue(done.wait(2))
        self.assertEqual(sent, [])
        self.assertEqual(reports, ['selected-target execute: blocked'])


class EjectDriveTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = FakeController()
        self.monitor = FakeMonitor()
        self.actuate = Mock()
        self.media = Mock(side_effect=[4, 2])
        self.sleep = Mock()
        self.identity = Mock(return_value=IDENTITY)

    def invoke(self, **kw):
        return eject_verified_idle(
            self.ctrl, self.monitor,
            identity_reader=kw.get('identity_reader', self.identity),
            lock=kw.get('lock', fake_lock),
            media=kw.get('media', self.media),
            actuate=self.actuate,
            path_exists=lambda _: True,
            sleep=self.sleep)

    def test_success_invalidates_selection_no_sonos_command(self):
        self.assertTrue(self.invoke())
        self.actuate.assert_called_once_with(DEVICE)
        self.assertEqual(self.ctrl.state.mode, Mode.NO_DISC)
        self.assertIsNone(self.ctrl.state.selected_track)
        self.assertIsNone(self.ctrl.display_toc)
        self.assertIsNone(self.monitor.loaded_toc)
        self.assertEqual(self.ctrl.disc_status.mode, DiscMode.EJECTED)
        self.assertIsNone(self.ctrl.owned_url)

    def test_reject_active_and_unsettled_states(self):
        for state in ('playing', 'paused', 'starting', 'owned', 'player',
                      'server', 'reader', 'joined', 'uncertain'):
            with self.subTest(state=state):
                self.setUp()
                if state in ('playing', 'paused', 'starting'):
                    self.ctrl.state = PlayerState(
                        {'playing': Mode.PLAYING, 'paused': Mode.PAUSED,
                         'starting': Mode.STARTING}[state], (1, 2), 1)
                elif state == 'owned':
                    self.ctrl.owned_url = 'own-url'
                elif state == 'player':
                    self.ctrl.player = object()
                elif state == 'server':
                    self.ctrl.server.running = True
                elif state == 'reader':
                    self.ctrl.server.reading = True
                elif state == 'joined':
                    self.ctrl._joined = {'member': object()}
                else:
                    self.ctrl._group_uncertain = True
                with self.assertRaises(RuntimeError):
                    self.invoke()
                self.actuate.assert_not_called()

    def test_unverified_drive_never_ejects(self):
        for kind in ('identity_missing', 'identity_changed', 'path_changed',
                     'disc_unready', 'monitor_disconnected', 'toc_missing'):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == 'identity_missing':
                    self.monitor.identity = None
                elif kind == 'identity_changed':
                    self.identity.return_value = object()
                elif kind == 'path_changed':
                    self.ctrl.server.device = '/dev/sr8'
                elif kind == 'disc_unready':
                    self.ctrl.disc_status = DiscStatus(DiscMode.NO_DISC, DEVICE)
                elif kind == 'monitor_disconnected':
                    self.monitor._disconnected = True
                elif kind == 'toc_missing':
                    self.monitor.loaded_toc = None
                with self.assertRaises(RuntimeError):
                    self.invoke()
                self.actuate.assert_not_called()

    def test_failed_media_probe_and_busy_lock_fail_closed(self):
        self.media.side_effect = None
        self.media.return_value = 1
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.actuate.assert_not_called()

        @contextlib.contextmanager
        def busy(device, *, blocking=False):
            yield False
        with self.assertRaises(RuntimeError):
            self.invoke(lock=busy)
        self.actuate.assert_not_called()

    def test_ioctl_success_without_open_does_not_acknowledge(self):
        self.media.side_effect = None
        self.media.return_value = 4
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
            self.invoke()
        self.actuate.assert_called_once_with(DEVICE)
        self.assertEqual(self.media.call_count, 6)
        self.assertEqual(self.sleep.call_count, 4)
        self.assertEqual(self.ctrl.state.mode, Mode.STOPPED)
        self.assertEqual(self.ctrl.disc_status.mode, DiscMode.READY)
        self.assertIs(self.monitor.loaded_toc, TOC)

    def test_delayed_tray_open_confirms_without_repeating_ioctl(self):
        self.media.side_effect = [4, 4, 4, 2]
        self.assertTrue(self.invoke())
        self.actuate.assert_called_once_with(DEVICE)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.ctrl.disc_status.mode, DiscMode.EJECTED)

    def test_eject_error_retains_previous_state_no_retry(self):
        self.actuate.side_effect = OSError('device rejected eject')
        with self.assertRaises(OSError):
            self.invoke()
        self.assertEqual(self.actuate.call_count, 1)
        self.assertEqual(self.ctrl.state.mode, Mode.STOPPED)
        self.assertEqual(self.ctrl.disc_status.mode, DiscMode.READY)

    def test_player_rpc_refuses_without_internal_drive_handler(self):
        reply = handle_command(self.ctrl, {'command': 'eject'})
        self.assertFalse(reply['ok'])
        self.assertIn('unavailable', reply['error'])

    def test_player_rpc_calls_accepted_handler_once(self):
        eject = Mock()
        reply = handle_command(self.ctrl, {'command': 'eject'}, eject=eject)
        self.assertTrue(reply['ok'])
        eject.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
