"""Disc detection: startup without disc, disc monitor and state transitions (offline)."""
import contextlib
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from sonoscdplayer.player import service as player_service
from sonoscdplayer.drive.disc_state import DiscMode, DiscStatus
from sonoscdplayer.drive.disc_startup import (
    inspect_initial_disc,
    classify_unavailable,
    CDS_NO_DISC,
    CDS_TRAY_OPEN,
    CDS_DRIVE_NOT_READY,
    CDS_DISC_OK,
)
from sonoscdplayer.player.state import PlayerState, Mode
from sonoscdplayer.player.controller import Controller
from sonoscdplayer.drive.disc_monitor import DiscMonitor, idle_for_disc_io
from sonoscdplayer.player.service import disc_allows_new_play
from disc_monitor_fakes import FakeController, Server, fake_lock, make_monitor, unavailable_lock  # noqa: F401



class FakeServer:
    def alive(self):
        return False

    def reader_active(self):
        return False


class DiscStateTests(unittest.TestCase):
    def test_six_states_exposed_independent_of_player_mode(self):
        self.assertEqual([state.value for state in DiscMode],
                         ['NO_DRIVE', 'NO_DISC', 'LOADING', 'READY',
                          'UNREADABLE', 'EJECTED'])
        self.assertEqual(PlayerState().mode, Mode.NO_DISC)

    def test_status_ready_requires_tracks_and_device(self):
        for args in ((DiscMode.READY,), (DiscMode.READY, '/dev/sr0', 0),
                     (DiscMode.NO_DISC, '/dev/sr0', 1),
                     (DiscMode.NO_DRIVE, '/dev/sr0')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                DiscStatus(*args)
        ready = DiscStatus.ready('/dev/sr1', 12)
        self.assertEqual(ready.snapshot(),
                         {'mode': 'READY', 'device': '/dev/sr1',
                          'track_count': 12, 'error': None})

    def test_absent_drive_never_runs_probe_or_toc(self):
        def forbidden(*args):
            raise AssertionError('must not inspect absent drive')
        state, toc = inspect_initial_disc(ROOT, candidates=(),
                                          detect=forbidden, read=forbidden)
        self.assertEqual(state.mode, DiscMode.NO_DRIVE)
        self.assertIsNone(toc)

    def test_unavailable_media_classification_is_conservative(self):
        expected = {CDS_NO_DISC: DiscMode.NO_DISC,
                    CDS_TRAY_OPEN: DiscMode.EJECTED,
                    CDS_DRIVE_NOT_READY: DiscMode.LOADING,
                    CDS_DISC_OK: DiscMode.UNREADABLE}
        for code, mode in expected.items():
            with self.subTest(code=code):
                result = classify_unavailable('/dev/sr0', lambda _: code,
                                              error='CD query failed')
                self.assertEqual(result.mode, mode)
                self.assertEqual(result.track_count, 0)
                self.assertEqual(result.error is not None,
                                 mode == DiscMode.UNREADABLE)

    def test_status_query_failure_not_misreported_as_no_disc(self):
        def unavailable(_):
            raise OSError('device busy')
        result = classify_unavailable('/dev/sr0', unavailable, error='probe failed')
        self.assertEqual(result.mode, DiscMode.UNREADABLE)

    def test_no_cd_still_has_device_and_no_stale_toc(self):
        def unavailable(_):
            raise RuntimeError('no readable audio CD')
        state, toc = inspect_initial_disc(ROOT, candidates=('/dev/sr0',),
            detect=unavailable, media_status=lambda _: CDS_NO_DISC)
        self.assertEqual(state.mode, DiscMode.NO_DISC)
        self.assertEqual(state.device, '/dev/sr0')
        self.assertEqual(state.track_count, 0)
        self.assertIsNone(toc)

    def test_ambiguous_multiple_drives_not_adopted(self):
        def unavailable(_):
            raise RuntimeError('no unique CD-DA drive')
        state, toc = inspect_initial_disc(ROOT,
            candidates=('/dev/sr0', '/dev/sr1'), detect=unavailable)
        self.assertEqual(state.mode, DiscMode.UNREADABLE)
        self.assertIsNone(state.device)
        self.assertIsNone(toc)

    def test_ready_uses_existing_detector_and_toc(self):
        disc = SimpleNamespace(tracks=(object(), object()))
        state, toc = inspect_initial_disc(ROOT, candidates=('/dev/sr1',),
            detect=lambda _: '/dev/sr1', read=lambda _: disc)
        self.assertEqual(state, DiscStatus.ready('/dev/sr1', 2))
        self.assertIs(toc, disc)

    def test_status_snapshot_disc_is_separate_and_read_only(self):
        controller = Controller(PlayerState(), None, None, 8765, FakeServer())
        self.assertNotIn('disc', controller.status_snapshot())
        controller.disc_status = DiscStatus(DiscMode.NO_DISC, '/dev/sr0')
        snapshot = controller.status_snapshot()
        self.assertEqual(snapshot['player']['mode'], 'NO_DISC')
        self.assertEqual(snapshot['disc']['mode'], 'NO_DISC')
        self.assertEqual(snapshot['stream']['active'], False)
        self.assertEqual(controller.state, PlayerState())

    def test_service_starts_without_drive_and_does_not_start_motor(self):
        with tempfile.TemporaryDirectory() as temp:
            config = pathlib.Path(temp) / 'config.toml'
            config.write_text('[service]\nshutdown_policy = "stop_owned"\n'
                              '[audio_startup]\nprebuffer_before_sonos = true\n'
                              'pcm_timeout_seconds = 45\n')
            socket_path = pathlib.Path(temp) / 'control.sock'
            observed = []

            def fake_serve(controller, path, **kwargs):
                observed.append((controller, path, kwargs,
                                 controller.status_snapshot()))

            with patch.object(player_service, 'inspect_initial_disc',
                              return_value=(DiscStatus(DiscMode.NO_DRIVE), None)), \
                 patch.object(player_service, 'DrivePowerManager') as power, \
                 patch.object(player_service, 'serve', side_effect=fake_serve):
                result = player_service.run(['--config', str(config),
                                             '--socket', str(socket_path)])
            self.assertEqual(result, 0)
            power.assert_not_called()
            self.assertEqual(len(observed), 1)
            _, _, kwargs, snapshot = observed[0]
            self.assertIsNone(kwargs['power'])
            self.assertIsNone(kwargs['drive_toc'])
            self.assertEqual(snapshot['disc']['mode'], 'NO_DRIVE')
            self.assertEqual(snapshot['player']['track_count'], 0)
            self.assertFalse(snapshot['stream']['active'])

    def test_missing_disc_rejects_play_without_sonos_contact(self):
        controller = Controller(PlayerState(), None, None, 8765, FakeServer(),
                                verify=lambda _: self.fail('Sonos was contacted'))
        controller.disc_status = DiscStatus(DiscMode.NO_DISC, '/dev/sr0')
        reply = player_service.handle_command(
            controller, {'command': 'play', 'uid': 'RINCON_123'})
        self.assertFalse(reply['ok'])
        self.assertIn('STOPPED', reply['error'])
        self.assertEqual(reply['state']['disc']['mode'], 'NO_DISC')


class DiscMonitorTests(unittest.TestCase):
    def test_insert_loads_toc_only_when_media_ready(self):
        c = FakeController()
        codes = [CDS_DRIVE_NOT_READY]
        disc = SimpleNamespace(tracks=[SimpleNamespace(number=1),
                                       SimpleNamespace(number=2)])
        m, calls = make_monitor(codes, disc)
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.LOADING)
        self.assertEqual(c.state.mode, Mode.NO_DISC)
        self.assertEqual(calls['toc'], 0)
        codes[0] = CDS_DISC_OK
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status, DiscStatus.ready('/dev/sr0', 2))
        self.assertEqual(c.state.track_numbers, (1, 2))
        self.assertEqual(c.state.mode, Mode.STOPPED)
        self.assertIs(m.loaded_toc, disc)
        self.assertFalse(m.poll(c))
        self.assertEqual(calls['toc'], 1)

    def test_eject_clears_stale_track_selection(self):
        c = FakeController()
        c.state = PlayerState().disc_loaded((1, 2, 3))
        c.disc_status = DiscStatus.ready('/dev/sr0', 3)
        m, calls = make_monitor([CDS_TRAY_OPEN])
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.EJECTED)
        self.assertEqual(c.state, PlayerState())
        self.assertEqual(c.disc_status.track_count, 0)
        self.assertIsNone(m.loaded_toc)
        self.assertEqual(calls['toc'], 0)

    def test_remove_then_insert_different_disc(self):
        c = FakeController()
        state = [CDS_DISC_OK]
        m, _ = make_monitor(state, SimpleNamespace(tracks=[SimpleNamespace(number=1)]))
        self.assertTrue(m.poll(c))
        state[0] = CDS_NO_DISC
        self.assertTrue(m.poll(c))
        self.assertEqual(c.state, PlayerState())
        self.assertEqual(c.disc_status.mode, DiscMode.NO_DISC)
        state[0] = CDS_DISC_OK
        self.assertTrue(m.poll(c))
        self.assertEqual(c.state.track_numbers, (1,))

    def test_unreadable_disc_has_no_old_tracks(self):
        c = FakeController()
        c.state = PlayerState().disc_loaded((1, 2))
        c.disc_status = DiscStatus.ready('/dev/sr0', 2)
        m, _ = make_monitor([CDS_TRAY_OPEN])
        m.poll(c)
        m.media = lambda _: CDS_DISC_OK
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.UNREADABLE)
        self.assertEqual(c.state, PlayerState())

    def test_no_probe_during_reader_or_owned_session(self):
        for unsafe in ('reader', 'running', 'owned', 'joined',
                       'uncertain', 'playing', 'starting', 'paused'):
            with self.subTest(unsafe=unsafe):
                c = FakeController()
                if unsafe == 'reader':
                    c.server.reader = True
                elif unsafe == 'running':
                    c.server.running = True
                elif unsafe == 'owned':
                    c.owned_url = 'http://owned'
                elif unsafe == 'joined':
                    c._joined = {'member': object()}
                elif unsafe == 'uncertain':
                    c._group_uncertain = True
                else:
                    c.state = PlayerState().disc_loaded((1,))
                    c.state = c.state.start_requested()
                    if unsafe == 'playing':
                        c.state = c.state.playback_confirmed()
                    if unsafe == 'paused':
                        c.state = c.state.playback_confirmed().paused()
                m, counts = make_monitor([CDS_NO_DISC])
                self.assertFalse(idle_for_disc_io(c))
                self.assertFalse(m.poll(c))
                self.assertEqual(counts, {'media': 0, 'toc': 0})

    def test_lock_contention_skips_io(self):
        c = FakeController()
        m, counts = make_monitor([CDS_DISC_OK], lock=unavailable_lock)
        self.assertFalse(m.poll(c))
        self.assertEqual(counts, {'media': 0, 'toc': 0})

    def test_usb_disconnect_no_other_device_adoption(self):
        c = FakeController()
        exists = [False]
        m, counts = make_monitor([CDS_DISC_OK])
        m.path_exists = lambda _: exists[0]
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.NO_DRIVE)
        first = c.disc_status.snapshot()
        self.assertFalse(m.poll(c))  # repeated missing path is not a transition
        self.assertEqual(c.disc_status.snapshot(), first)
        exists[0] = True
        self.assertFalse(m.poll(c))  # same node is still unverified after reconnect
        self.assertEqual(c.disc_status.snapshot(), first)
        self.assertEqual(counts['media'], 0)
        self.assertEqual(c.disc_status.mode, DiscMode.NO_DRIVE)

    def test_usb_identity_mismatch_refuses_access(self):
        c = FakeController()
        m, counts = make_monitor([CDS_DISC_OK])
        m.identity = object()
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.NO_DRIVE)
        status = c.disc_status.snapshot()
        self.assertIn('verified identity', status['error'])
        self.assertFalse(m.poll(c))  # no repeated status changes or media reads
        self.assertEqual(c.disc_status.snapshot(), status)
        self.assertEqual(counts['media'], 0)

    def test_ready_disc_not_probed_when_stream_owns_session(self):
        c = FakeController()
        c.state = PlayerState().disc_loaded((1,))
        c.disc_status = DiscStatus.ready('/dev/sr0', 1)
        c.server.running = True
        m, counts = make_monitor([CDS_TRAY_OPEN])
        self.assertFalse(m.poll(c))
        self.assertEqual(c.state.track_numbers, (1,))
        self.assertEqual(counts['media'], 0)


    def test_same_serial_reconnect_with_different_disc(self):
        c = FakeController()
        c.state = PlayerState().disc_loaded((1, 2, 3))
        c.disc_status = DiscStatus.ready('/dev/sr0', 3)
        expected_id = object()
        present = [False]
        media_calls = []
        rebindings = []
        new_disc = SimpleNamespace(tracks=[
            SimpleNamespace(number=i) for i in range(1, 6)])
        m = DiscMonitor('/dev/sr0', expected_id,
                        media=lambda p: media_calls.append(p) or CDS_DISC_OK,
                        toc=lambda _: new_disc,
                        path_exists=lambda _: present[0],
                        identity_reader=lambda _: expected_id,
                        recover=lambda _: '/dev/sr0',
                        on_rebind=rebindings.append, lock=fake_lock)
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.NO_DRIVE)
        self.assertEqual(c.state.track_numbers, ())
        present[0] = True
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.LOADING)
        self.assertEqual(rebindings, ['/dev/sr0'])
        self.assertEqual(media_calls, [])
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status.track_count, 5)
        self.assertEqual(c.state.track_numbers, (1, 2, 3, 4, 5))
        self.assertEqual(media_calls, ['/dev/sr0'])

    def test_reconnect_renumber_only_matching_identity_adopted(self):
        c = FakeController()
        expected_id = object()
        device_paths = {'/dev/sr0': False, '/dev/sr1': True}
        observed = []
        m = DiscMonitor('/dev/sr0', expected_id,
                        media=lambda _: CDS_NO_DISC,
                        path_exists=lambda p: device_paths[p],
                        identity_reader=lambda p: expected_id if p == '/dev/sr1' else None,
                        recover=lambda _: '/dev/sr1',
                        on_rebind=observed.append, lock=fake_lock)
        self.assertTrue(m.poll(c))
        self.assertTrue(m.poll(c))
        self.assertEqual(m.device, '/dev/sr1')
        self.assertEqual(observed, ['/dev/sr1'])
        self.assertTrue(m.poll(c))
        self.assertEqual(c.disc_status, DiscStatus(DiscMode.NO_DISC, '/dev/sr1'))

    def test_wrong_identity_reconnect_does_not_touch_media(self):
        c = FakeController()
        expected_id = object()
        counts = {'media': 0, 'rebind': 0}
        m = DiscMonitor('/dev/sr0', expected_id,
                        path_exists=lambda _: True,
                        identity_reader=lambda _: None,
                        recover=lambda _: '/dev/sr1',
                        media=lambda _: counts.__setitem__('media', counts['media']+1),
                        on_rebind=lambda _: counts.__setitem__('rebind', counts['rebind']+1),
                        lock=fake_lock)
        self.assertTrue(m.poll(c))
        self.assertFalse(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.NO_DRIVE)
        self.assertEqual(counts, {'media': 0, 'rebind': 0})

    def test_reconnect_no_serial_does_not_adopt_unknown_device(self):
        c = FakeController()
        m, counts = make_monitor([CDS_DISC_OK])
        m.path_exists = lambda _: False
        self.assertTrue(m.poll(c))
        m.path_exists = lambda _: True
        self.assertFalse(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.NO_DRIVE)
        self.assertEqual(counts['media'], 0)

    def test_reconnect_never_runs_during_active_session(self):
        c = FakeController()
        m, counts = make_monitor([CDS_DISC_OK])
        m.path_exists = lambda _: False
        self.assertTrue(m.poll(c))
        c.owned_url = 'http://owned'
        self.assertFalse(m.poll(c))
        self.assertEqual(counts['media'], 0)

    def test_rebind_failure_preserves_unavailable_state(self):
        c = FakeController()
        expected_id = object()
        present = [False]
        def failed_rebind(_):
            raise RuntimeError('cannot update power manager')
        m = DiscMonitor('/dev/sr0', expected_id, path_exists=lambda _: present[0],
                        identity_reader=lambda _: expected_id,
                        recover=lambda _: '/dev/sr0',
                        on_rebind=failed_rebind, lock=fake_lock)
        self.assertTrue(m.poll(c))
        present[0] = True
        self.assertFalse(m.poll(c))
        self.assertEqual(c.disc_status.mode, DiscMode.NO_DRIVE)
        self.assertTrue(m._disconnected)


    def test_play_boundary_unchanged_toc_is_approved(self):
        c = FakeController()
        disc = SimpleNamespace(tracks=[SimpleNamespace(number=1)],
                               leadout_lsn=18000)
        c.state = PlayerState().disc_loaded((1,))
        c.disc_status = DiscStatus.ready('/dev/sr0', 1)
        identity = object()
        calls = []
        m = DiscMonitor('/dev/sr0', identity,
                        media=lambda _: calls.append('media') or CDS_DISC_OK,
                        toc=lambda _: calls.append('toc') or disc,
                        identity_reader=lambda _: identity,
                        path_exists=lambda _: True, lock=fake_lock)
        m.loaded_toc = disc
        self.assertTrue(m.validate_before_play(c))
        self.assertEqual(c.state.selected_track, 1)
        self.assertEqual(calls, ['media', 'toc'])

    def test_play_boundary_different_disc_same_track_count_continues_explicit_play(self):
        c = FakeController()
        old = SimpleNamespace(tracks=[SimpleNamespace(number=1)],
                              leadout_lsn=18000)
        new = SimpleNamespace(tracks=[SimpleNamespace(number=1)],
                              leadout_lsn=19000)
        identity = object()
        c.state = PlayerState().disc_loaded((1,))
        c.disc_status = DiscStatus.ready('/dev/sr0', 1)
        m = DiscMonitor('/dev/sr0', identity, media=lambda _: CDS_DISC_OK,
                        toc=lambda _: new, identity_reader=lambda _: identity,
                        path_exists=lambda _: True, lock=fake_lock)
        m.loaded_toc = old
        self.assertTrue(m.validate_before_play(c))
        self.assertIs(m.loaded_toc, new)
        self.assertEqual(c.state.mode, Mode.STOPPED)
        self.assertEqual(c.state.selected_track, 1)
        self.assertTrue(m.validate_before_play(c))

    def test_play_boundary_missing_media_clears_track_selection(self):
        c = FakeController()
        disc = SimpleNamespace(tracks=[SimpleNamespace(number=1)])
        c.state = PlayerState().disc_loaded((1,))
        c.disc_status = DiscStatus.ready('/dev/sr0', 1)
        identity = object()
        m = DiscMonitor('/dev/sr0', identity, media=lambda _: CDS_NO_DISC,
                        toc=lambda _: self.fail('TOC must not be queried'),
                        identity_reader=lambda _: identity,
                        path_exists=lambda _: True, lock=fake_lock)
        m.loaded_toc = disc
        self.assertFalse(m.validate_before_play(c))
        self.assertEqual(c.state, PlayerState())
        self.assertEqual(c.disc_status.mode, DiscMode.LOADING)

    def test_play_boundary_missing_usb_identity_fails_closed(self):
        c = FakeController()
        c.state = PlayerState().disc_loaded((1,))
        c.disc_status = DiscStatus.ready('/dev/sr0', 1)
        original = object()
        m = DiscMonitor('/dev/sr0', original, media=lambda _: self.fail('media forbidden'),
                        identity_reader=lambda _: None,
                        path_exists=lambda _: True, lock=fake_lock)
        self.assertFalse(m.validate_before_play(c))
        self.assertEqual(c.disc_status.mode, DiscMode.NO_DRIVE)
        self.assertEqual(c.state, PlayerState())

    def test_play_boundary_no_probe_during_owned_session(self):
        c = FakeController()
        c.owned_url = 'http://owned'
        m, counts = make_monitor([CDS_DISC_OK])
        self.assertFalse(m.validate_before_play(c))
        self.assertEqual(counts, {'media': 0, 'toc': 0})


    def test_play_command_guard_refreshes_stale_disc_without_second_play(self):
        c = FakeController()
        c.state = PlayerState().disc_loaded((1,))
        c.disc_status = DiscStatus.ready('/dev/sr0', 1)
        identity = object()
        old = SimpleNamespace(tracks=[SimpleNamespace(number=1)],
                              leadout_lsn=12000)
        new = SimpleNamespace(tracks=[SimpleNamespace(number=1)],
                              leadout_lsn=16000)
        m = DiscMonitor('/dev/sr0', identity, media=lambda _: CDS_DISC_OK,
                        toc=lambda _: new, identity_reader=lambda _: identity,
                        path_exists=lambda _: True, lock=fake_lock)
        m.loaded_toc = old
        for cmd in ('status', 'pause', 'resume'):
            self.assertTrue(disc_allows_new_play(c, {'command': cmd}, m))
        self.assertTrue(disc_allows_new_play(
            c, {'command': 'play', 'uid': 'RINCON_123'}, m))
        self.assertIs(m.loaded_toc, new)
        self.assertEqual(c.state.selected_track, 1)
        self.assertTrue(disc_allows_new_play(
            c, {'command': 'play-selected'}, m))

    def test_playpause_guard_runs_only_for_stopped_player(self):
        c = FakeController()
        c.state = PlayerState().disc_loaded((1,))
        c.disc_status = DiscStatus.ready('/dev/sr0', 1)
        class Monitor:
            def validate_before_play(self, ctrl):
                raise AssertionError('playback session must not probe disc')
        c.state = c.state.start_requested().playback_confirmed()
        self.assertTrue(disc_allows_new_play(
            c, {'command': 'playpause', 'uid': 'RINCON_123'}, Monitor()))


    def test_play_swapped_disc_resets_old_selected_track_to_first(self):
        c = FakeController()
        c.state = PlayerState().disc_loaded((1, 2, 3)).select(3)
        c.disc_status = DiscStatus.ready('/dev/sr0', 3)
        original_id = object()
        original = SimpleNamespace(tracks=[SimpleNamespace(number=i) for i in (1, 2, 3)],
                                   leadout_lsn=75000)
        replacement = SimpleNamespace(tracks=[SimpleNamespace(number=i) for i in (1, 2)],
                                      leadout_lsn=45000)
        m = DiscMonitor('/dev/sr0', original_id,
                        media=lambda _: CDS_DISC_OK,
                        toc=lambda _: replacement,
                        path_exists=lambda _: True,
                        identity_reader=lambda _: original_id,
                        lock=fake_lock)
        m.loaded_toc = original
        self.assertTrue(disc_allows_new_play(
            c, {'command': 'play', 'uid': 'RINCON_123'}, m))
        self.assertEqual(c.state.mode, Mode.STOPPED)
        self.assertEqual(c.state.track_numbers, (1, 2))
        self.assertEqual(c.state.selected_track, 1)
        self.assertEqual(c.disc_status.track_count, 2)

    def test_play_swapped_disc_unreadable_is_rejected(self):
        c = FakeController()
        c.state = PlayerState().disc_loaded((1,))
        c.disc_status = DiscStatus.ready('/dev/sr0', 1)
        original_id = object()
        m = DiscMonitor('/dev/sr0', original_id,
                        media=lambda _: CDS_DISC_OK,
                        toc=lambda _: (_ for _ in ()).throw(ValueError('unreadable')),
                        path_exists=lambda _: True,
                        identity_reader=lambda _: original_id,
                        lock=fake_lock)
        m.loaded_toc = SimpleNamespace(tracks=[SimpleNamespace(number=1)])
        self.assertFalse(disc_allows_new_play(
            c, {'command': 'play', 'uid': 'RINCON_123'}, m))
        self.assertEqual(c.state, PlayerState())
        self.assertEqual(c.disc_status.mode, DiscMode.UNREADABLE)


if __name__ == '__main__':
    unittest.main()
