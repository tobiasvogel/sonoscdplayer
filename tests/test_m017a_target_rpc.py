"""M0.17a: control socket selection RPC is local-only and fail-closed."""
import unittest
from types import SimpleNamespace
from player_service import handle_command
from player_target_selection import SelectedTarget, verify_solo_stopped
from player_state import Mode


class FakeController:
    def __init__(self, mode=Mode.STOPPED):
        self.state = SimpleNamespace(mode=mode)
        self.owned_url = None
        self.calls = []

    def status(self):
        return 'stopped'

    def status_snapshot(self):
        return {'schema_version':1,'player':{'mode':self.state.mode.value}}

    def command(self, command):
        self.calls.append(command)


class TargetSelectionRPCTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.checked = []
        self.selection = SelectedTarget(verify=lambda uid:self.checked.append(uid))

    def request(self, command, **kwargs):
        return handle_command(self.controller, {'command':command,**kwargs}, self.selection)

    def test_set_target_checks_and_does_not_play(self):
        reply=self.request('set-target',uid='RINCON_123')
        self.assertTrue(reply['ok'])
        self.assertTrue(reply['target_selection']['selected'])
        self.assertNotIn('RINCON_123',str(reply))
        self.assertEqual(self.checked,['RINCON_123'])
        self.assertEqual(self.controller.calls,[])

    def test_get_target_exposes_no_uid(self):
        self.request('set-target',uid='RINCON_123')
        reply=self.request('get-target')
        self.assertEqual(reply['target_selection'],{'selected':True,'ready_for_playback':False})
        self.assertNotIn('RINCON_123',str(reply))

    def test_clear_target_no_audio_operation(self):
        self.request('set-target',uid='RINCON_123')
        reply=self.request('clear-target')
        self.assertTrue(reply['ok'])
        self.assertFalse(reply['target_selection']['selected'])
        self.assertEqual(self.controller.calls,[])

    def test_rejected_verification_does_not_change_selection(self):
        self.request('set-target',uid='RINCON_123')
        self.selection._verify=lambda uid: (_ for _ in ()).throw(RuntimeError('grouped'))
        reply=self.request('set-target',uid='RINCON_234')
        self.assertFalse(reply['ok'])
        self.assertEqual(self.selection.uid_for_explicit_action(),'RINCON_123')

    def test_active_session_refuses_selection_and_clear(self):
        self.request('set-target',uid='RINCON_123')
        self.controller.state.mode=Mode.PLAYING
        self.assertFalse(self.request('clear-target')['ok'])
        self.assertFalse(self.request('set-target',uid='RINCON_234')['ok'])
        self.assertEqual(self.selection.uid_for_explicit_action(),'RINCON_123')

    def test_uid_not_allowed_in_get_or_clear(self):
        self.assertFalse(self.request('get-target',uid='RINCON_123')['ok'])
        self.assertFalse(self.request('clear-target',uid='RINCON_123')['ok'])

    def test_no_implicit_playpause_target(self):
        self.request('set-target',uid='RINCON_123')
        self.assertFalse(self.request('playpause')['ok'])
        self.assertEqual(self.controller.calls,[])

    def test_bad_uid_rejected(self):
        self.assertFalse(self.request('set-target',uid='bad uid')['ok'])
        self.assertFalse(self.selection.summary()['selected'])

    def test_solo_live_preflight_checks_stopped(self):
        from unittest.mock import patch
        player=SimpleNamespace(uid='RINCON_123')
        player.group=SimpleNamespace(coordinator=player,members=(player,))
        player.get_current_transport_info=lambda: {'current_transport_state':'STOPPED'}
        with patch('sonos_parallel_test.find_target',return_value=player):
            self.assertTrue(verify_solo_stopped('RINCON_123'))
            player.group.members=(player,SimpleNamespace(uid='other'))
            with self.assertRaises(RuntimeError):
                verify_solo_stopped('RINCON_123')


if __name__ == '__main__':
    unittest.main()
