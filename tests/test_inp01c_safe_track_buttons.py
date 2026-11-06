"""INP-01c safe physical track buttons; offline-only tests."""
import contextlib
import io
import threading
import unittest

from integrations.interfaces.interface_input import handle_event, TRANSPORT_RESPONSE_TIMEOUTS
from integrations.interfaces.safe_button_execute import SelectedTargetExecutor
from integrations.interfaces.safe_button_policy import plan_track_change


def status(mode='PLAYING', owned=True, active=True, pending=False):
    return {'ok': True, 'state': {
        'schema_version': 1,
        'player': {'mode': mode},
        'sonos': {'owned': owned, 'group_cleanup_pending': pending},
        'stream': {'active': active},
    }}


def target(selected=True):
    return {'ok': True, 'target_selection': {'selected': selected}}


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

    def test_busy_second_press_dropped(self):
        entered, release, done = (threading.Event() for _ in range(3))
        sends = []
        def reader(path, command):
            if command == 'status':
                entered.set()
                release.wait(2)
                return status()
            return target()
        executor = SelectedTargetExecutor('/offline', reader=reader,
                   sender=lambda *args: sends.append(args),
                   report=lambda _: done.set())
        self.assertTrue(executor.submit('next'))
        self.assertTrue(entered.wait(2))
        self.assertFalse(executor.submit('previous'))
        release.set()
        self.assertTrue(done.wait(2))
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


if __name__ == '__main__':
    unittest.main()
