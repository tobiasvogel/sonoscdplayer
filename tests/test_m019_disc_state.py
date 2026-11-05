"""M0.19a offline tests: startup without disc, separate status, no auto PLAY."""
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from disc_state import DiscMode, DiscStatus
from disc_startup import (inspect_initial_disc, classify_unavailable,
                          CDS_NO_DISC, CDS_TRAY_OPEN, CDS_DRIVE_NOT_READY,
                          CDS_DISC_OK)
from player_state import PlayerState, Mode
from player_controller import Controller
import player_service


class FakeServer:
    def alive(self):
        return False

    def reader_active(self):
        return False


class DiscStateM019Tests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
