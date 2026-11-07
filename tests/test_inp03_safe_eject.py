"""INP-03: offline-only safe physical EJECT tests; never access a real drive."""
import contextlib
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from disc_state import DiscMode, DiscStatus
from player_state import Mode, PlayerState
from safe_drive_eject import CDROM_LOCKDOOR, CDROMEJECT, eject_tray, eject_verified_idle
from player_service import handle_command
from integrations.interfaces.button_commands import map_event
from integrations.interfaces.interface_input import handle_event, TRANSPORT_RESPONSE_TIMEOUTS
from integrations.interfaces.safe_button_execute import SelectedTargetExecutor
from integrations.interfaces.safe_button_policy import plan_eject


DEVICE = '/dev/sr9'
IDENTITY = object()
TOC = object()


def snapshot(mode='STOPPED', owned=False, active=False, pending=False, disc='READY'):
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
        return snapshot()['state']


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
        with patch('safe_drive_eject.os.open', return_value=17) as op, \
                patch('safe_drive_eject.fcntl.ioctl') as ioctl, \
                patch('safe_drive_eject.os.close') as close:
            eject_tray(DEVICE)
        self.assertEqual(op.call_count, 1)
        self.assertEqual(ioctl.call_args_list, [
            unittest.mock.call(17, CDROM_LOCKDOOR, 0),
            unittest.mock.call(17, CDROMEJECT),
        ])
        close.assert_called_once_with(17)

    def test_unlock_rejection_prevents_eject_and_closes_descriptor(self):
        with patch('safe_drive_eject.os.open', return_value=17), \
                patch('safe_drive_eject.fcntl.ioctl',
                      side_effect=OSError('unlock rejected')) as ioctl, \
                patch('safe_drive_eject.os.close') as close:
            with self.assertRaisesRegex(OSError, 'unlock rejected'):
                eject_tray(DEVICE)
        ioctl.assert_called_once_with(17, CDROM_LOCKDOOR, 0)
        close.assert_called_once_with(17)

    def test_eject_rejection_closes_descriptor_without_retry(self):
        with patch('safe_drive_eject.os.open', return_value=17), \
                patch('safe_drive_eject.fcntl.ioctl',
                      side_effect=[0, OSError('eject rejected')]) as ioctl, \
                patch('safe_drive_eject.os.close') as close:
            with self.assertRaisesRegex(OSError, 'eject rejected'):
                eject_tray(DEVICE)
        self.assertEqual(ioctl.call_count, 2)
        close.assert_called_once_with(17)


class EjectPolicyTests(unittest.TestCase):
    def test_idle_ready_permitted_without_target(self):
        self.assertEqual(plan_eject(snapshot()), 'eject')

    def test_fail_closed(self):
        bad = [
            snapshot(mode='PLAYING', owned=True, active=True),
            snapshot(mode='PAUSED', owned=True, active=True),
            snapshot(mode='STARTING'),
            snapshot(mode='NO_DISC', disc='NO_DISC'),
            snapshot(owned=True),
            snapshot(active=True),
            snapshot(pending=True),
            snapshot(disc='EJECTED'),
            {'ok': False},
            None,
        ]
        for item in bad:
            with self.subTest(item=item):
                self.assertIsNone(plan_eject(item))
        s = snapshot()
        s['state']['schema_version'] = 2
        self.assertIsNone(plan_eject(s))
        s = snapshot()
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
            return snapshot()

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
            '/offline', reader=lambda *_: snapshot(mode='PLAYING'),
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
