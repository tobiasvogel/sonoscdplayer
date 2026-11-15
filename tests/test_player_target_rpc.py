"""Target selection and play-selected RPCs on the control socket (local-only, fail-closed)."""
import unittest
from dataclasses import replace
from types import SimpleNamespace, SimpleNamespace as NS
from player_service import handle_command
from player_target_selection import SelectedTarget, verify_solo_stopped
from player_state import Mode
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.multi_target import from_devices



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


def accepted_notice(menu, now):
    current = menu.connect_notices.current(now=now)
    return (current.key, current.severity) if current is not None else None


class Controller:
    def __init__(self):
        self.state = NS(mode=Mode.STOPPED)
        self.owned_url = None
        self.calls = []
    def status(self): return 'idle'
    def status_snapshot(self): return {'schema_version': 1}
    def play(self, uid, members=None):
        self.calls.append((uid, tuple(members or ())))


class GroupPlaySelectedTests(unittest.TestCase):
    def test_group_selection_is_not_playback(self):
        c = Controller()
        s = SelectedTarget(verify=lambda uid: True, find_many=lambda ids: {
            'B': NS(uid='B', group=NS(coordinator=NS(uid='B'),members=[NS(uid='B')]),
                    get_current_transport_info=lambda:{'current_transport_state':'STOPPED'})})
        reply = handle_command(c, {'command':'set-target','uid':'A','members':['B']},s)
        self.assertTrue(reply['ok'],reply)
        self.assertEqual(c.calls,[])
        self.assertEqual(s.members_for_explicit_action(),('B',))

    def test_only_explicit_play_selected_uses_saved_members(self):
        c = Controller()
        s = SelectedTarget(verify=lambda uid: True)
        s.set(c,'A')
        self.assertEqual(c.calls,[])
        self.assertTrue(handle_command(c, {'command':'play-selected'},s)['ok'])
        self.assertEqual(c.calls,[('A',())])

    def test_group_play_selected_passes_members_to_controller(self):
        c = Controller()
        s = SelectedTarget(verify=lambda uid: True)
        s._uid = 'A'
        s._members = ('B','C')
        reply = handle_command(c, {'command':'play-selected'},s)
        self.assertTrue(reply['ok'],reply)
        self.assertEqual(c.calls,[('A',('B','C'))])

    def test_play_selected_requires_prior_selection(self):
        c=Controller()
        s=SelectedTarget(verify=lambda uid: True)
        self.assertFalse(handle_command(c,{'command':'play-selected'},s)['ok'])
        self.assertEqual(c.calls,[])

    def test_play_selected_rejects_uid_in_request(self):
        c=Controller()
        s=SelectedTarget(verify=lambda uid: True)
        self.assertFalse(handle_command(c,{'command':'play-selected','uid':'A'},s)['ok'])
        self.assertEqual(c.calls,[])

    def test_normal_play_cannot_supply_members(self):
        c=Controller()
        self.assertFalse(handle_command(c,{'command':'play','uid':'A','members':['B']})['ok'])
        self.assertEqual(c.calls,[])

    def test_oled_group_confirm_sends_only_selection_intent(self):
        a=NS(uid='A',player_name='Alpha')
        b=NS(uid='B',player_name='Beta')
        a.group=NS(coordinator=a,members=(a,))
        b.group=NS(coordinator=b,members=(b,))
        topology=from_devices((a,b))
        seen=[]
        menu=InteractiveMenu(discover=lambda **kw:topology, target_rpc=lambda uid:True,
                             group_target_rpc=lambda uid,members:seen.append((uid,members)) or True)
        menu.active=True
        menu.state=menu.state.update(topology)
        menu.state=replace(menu.state,draft=frozenset(('A','B')),applied=frozenset(('A','B')))
        menu._send_target()
        menu._target_worker.join(timeout=2)
        menu.poll(now=0)
        self.assertEqual(seen,[('A',('B',))])
        self.assertEqual(accepted_notice(menu,0),('status.target_accepted','success'))


if __name__ == '__main__':
    unittest.main()
