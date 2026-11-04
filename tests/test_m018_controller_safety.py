"""M0.18 controller ownership/race and group recovery gates; no Sonos/GPIO I/O."""
import unittest
from types import SimpleNamespace
from unittest import mock

from player_controller import Controller
from player_state import Mode, PlayerState
import sonos_group


UID = 'RINCON_REVIEW_COORD'
MEMBER_UID = 'RINCON_REVIEW_MEMBER'
URL = 'http://192.168.1.10:8765/track/1.wav'
FOREIGN_URL = 'https://example.invalid/foreign.mp3'


class FakeSonos:
    def __init__(self, uid=UID, *, transport='PLAYING', uri=URL):
        self.uid = uid
        self.transport = transport
        self.uri = uri
        self.group = SimpleNamespace(coordinator=self, members=[self])
        self.operations = []
        self.transport_reads = 0
        self.uri_reads = 0
        self.on_transport_read = None
        self.on_uri_read = None

    def get_current_transport_info(self):
        self.transport_reads += 1
        if self.on_transport_read:
            self.on_transport_read(self.transport_reads)
        return {'current_transport_state': self.transport}

    def get_current_track_info(self):
        self.uri_reads += 1
        if self.on_uri_read:
            self.on_uri_read(self.uri_reads)
        return {'uri': self.uri}

    def pause(self):
        self.operations.append('pause')

    def play(self):
        self.operations.append('play')

    def stop(self):
        self.operations.append('stop')

    def unjoin(self):
        self.operations.append('unjoin')


class FakeServer:
    def __init__(self):
        self.running = True
        self.closed = False

    def alive(self):
        return self.running

    def close(self):
        self.closed = True
        self.running = False


def owned_controller(mode=Mode.PLAYING, player=None):
    speaker = player or FakeSonos(transport=(
        'PAUSED_PLAYBACK' if mode == Mode.PAUSED else 'PLAYING'))
    server = FakeServer()
    ctrl = Controller(PlayerState(mode, (1,), 1), UID, '192.168.1.10',
                      8765, server)
    ctrl.player = speaker
    ctrl.owned_url = URL
    ctrl.seen_playing = True
    ctrl._group_members = frozenset()
    return ctrl, speaker, server


class OwnershipChangeTests(unittest.TestCase):
    def test_foreign_uri_between_two_checks_blocks_pause_resume_stop(self):
        for mode, action in ((Mode.PLAYING, 'pause'),
                             (Mode.PAUSED, 'resume'),
                             (Mode.PLAYING, 'stop'),
                             (Mode.PAUSED, 'stop')):
            with self.subTest(mode=mode, action=action):
                ctrl, speaker, server = owned_controller(mode)
                speaker.on_uri_read = lambda n: setattr(
                    speaker, 'uri', FOREIGN_URL) if n == 2 else None
                with self.assertRaises(RuntimeError):
                    getattr(ctrl, action)()
                self.assertEqual(speaker.operations, [])
                self.assertEqual(speaker.uri_reads, 2)
                self.assertFalse(server.closed)

    def test_foreign_transport_before_command_blocks_mutation(self):
        for mode, action in ((Mode.PLAYING, 'pause'),
                             (Mode.PAUSED, 'resume'),
                             (Mode.PLAYING, 'stop')):
            with self.subTest(mode=mode, action=action):
                ctrl, speaker, _ = owned_controller(mode)
                speaker.on_transport_read = lambda n: setattr(
                    speaker, 'transport', 'STOPPED') if n == 2 else None
                with self.assertRaises(RuntimeError):
                    getattr(ctrl, action)()
                self.assertEqual(speaker.operations, [])

    def test_changed_group_topology_blocks_stop_without_unjoin(self):
        ctrl, speaker, server = owned_controller()
        other = FakeSonos(uid='RINCON_FOREIGN')
        # Change the topology after proof #1, before proof #2 begins.
        speaker.on_uri_read = lambda n: setattr(
            speaker.group, 'members', [speaker, other]) if n == 1 else None
        with self.assertRaises(RuntimeError):
            ctrl.stop()
        self.assertEqual(speaker.operations, [])
        self.assertFalse(server.closed)

    def test_wrong_uid_blocks_without_side_effects(self):
        ctrl, speaker, _ = owned_controller()
        speaker.uid = 'RINCON_FOREIGN'
        with self.assertRaises(RuntimeError):
            ctrl.stop()
        self.assertEqual(speaker.operations, [])

    def test_shutdown_refuses_changed_uri_and_does_not_stop(self):
        ctrl, speaker, _ = owned_controller()
        speaker.on_uri_read = lambda n: setattr(
            speaker, 'uri', FOREIGN_URL) if n == 2 else None
        result = ctrl.shutdown_owned()
        self.assertEqual(result['action'], 'not_stopped')
        self.assertEqual(speaker.operations, [])

    def test_player_service_command_ignores_stale_button_plan(self):
        # A button may have read owned PLAYING, but the actual controller
        # must independently reject a foreign URI before a later STOP.
        from player_service import handle_command
        ctrl, speaker, _ = owned_controller()
        speaker.uri = FOREIGN_URL
        reply = handle_command(ctrl, {'command': 'stop'})
        self.assertIs(reply['ok'], False)
        self.assertEqual(speaker.operations, [])


class GroupRecoveryTests(unittest.TestCase):
    def _group(self):
        coordinator = FakeSonos()
        member = FakeSonos(uid=MEMBER_UID)
        coordinator.group = SimpleNamespace(
            coordinator=coordinator, members=[coordinator, member])
        member.group = coordinator.group
        return coordinator, member

    def test_foreign_coordinator_uri_never_unjoins_member(self):
        coordinator, member = self._group()
        coordinator.uri = FOREIGN_URL
        outcome = sonos_group.release(
            coordinator, {MEMBER_UID: member}, owned_url=URL)
        self.assertEqual(outcome[MEMBER_UID], 'kept: foreign playback')
        self.assertEqual(member.operations, [])

    def test_changed_group_topology_never_unjoins_member(self):
        coordinator, member = self._group()
        coordinator.group.members.append(FakeSonos(uid='RINCON_FOREIGN'))
        outcome = sonos_group.release(
            coordinator, {MEMBER_UID: member}, owned_url=URL)
        self.assertEqual(outcome[MEMBER_UID], 'kept: topology changed')
        self.assertEqual(member.operations, [])

    def test_failed_member_unjoin_remains_pending(self):
        coordinator, member = self._group()
        def fail():
            raise TimeoutError('unjoin outcome unknown')
        member.unjoin = fail
        outcome = sonos_group.release(
            coordinator, {MEMBER_UID: member}, owned_url=URL)
        remaining = sonos_group.unresolved_members(
            {MEMBER_UID: member}, outcome)
        self.assertEqual(list(remaining), [MEMBER_UID])
        self.assertEqual(outcome[MEMBER_UID], 'kept: unjoin failed')

    def test_controller_does_not_recover_without_exact_owned_uri(self):
        coordinator, member = self._group()
        ctrl, _, _ = owned_controller()
        ctrl.state = PlayerState(Mode.STOPPED, (1,), 1)
        ctrl.player = None
        ctrl.owned_url = None
        ctrl._joined = {MEMBER_UID: member}
        ctrl._group_coord = coordinator
        ctrl._group_uncertain = True
        ctrl._recovery_url = URL
        coordinator.uri = FOREIGN_URL
        with mock.patch('sonos_group.inspect_recovery', return_value={
            'state': 'attached', 'standalone': (),
            'attached': (MEMBER_UID,), 'blocked': (),
        }), mock.patch('sonos_group.release') as release:
            with self.assertRaisesRegex(RuntimeError, 'ownership changed'):
                ctrl.resolve_group_recovery()
        release.assert_not_called()
        self.assertEqual(list(ctrl._joined), [MEMBER_UID])
        self.assertTrue(ctrl._group_uncertain)

    def test_blocked_recovery_never_mutates_group(self):
        coordinator, member = self._group()
        ctrl, _, _ = owned_controller()
        ctrl.state = PlayerState(Mode.STOPPED, (1,), 1)
        ctrl.player = None
        ctrl.owned_url = None
        ctrl._joined = {MEMBER_UID: member}
        ctrl._group_coord = coordinator
        ctrl._group_uncertain = True
        ctrl._recovery_url = URL
        with mock.patch('sonos_group.inspect_recovery', return_value={
            'state': 'blocked', 'standalone': (),
            'attached': (), 'blocked': (MEMBER_UID,),
        }), mock.patch('sonos_group.release') as release:
            with self.assertRaisesRegex(RuntimeError, 'blocked'):
                ctrl.resolve_group_recovery()
        release.assert_not_called()
        self.assertEqual(list(ctrl._joined), [MEMBER_UID])


if __name__ == '__main__':
    unittest.main()
