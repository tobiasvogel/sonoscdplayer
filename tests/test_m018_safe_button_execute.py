"""M0.18 isolated execute-path tests; never connect to hardware or Sonos."""
import contextlib
import io
import threading
import unittest

from integrations.interfaces.interface_input import handle_event
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


def run_one(reader, sender):
    log, done = [], threading.Event()

    def report(message):
        log.append(message)
        done.set()

    worker = SelectedTargetExecutor('/not-a-socket', reader=reader,
                                    sender=sender, report=report)
    assert worker.submit()
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
            def submit(self):
                safe.append('safe')

        class Legacy:
            def submit(self, cmd):
                legacy.append(cmd)

        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('playpause', 'pressed', commands=True, execute=True,
                         target_executor=Safe(), dispatcher=Legacy())
            handle_event('playpause', 'released', commands=True, execute=True,
                         target_executor=Safe(), dispatcher=Legacy())
        self.assertEqual(safe, ['safe'])
        self.assertEqual(legacy, [])


if __name__ == '__main__':
    unittest.main()
