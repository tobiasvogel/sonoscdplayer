"""Offline guardrails for explicit playback from previously selected group intent."""
import unittest
from types import SimpleNamespace as NS
from dataclasses import replace
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.multi_target import from_devices
from player_service import handle_command
from player_target_selection import SelectedTarget
from player_state import Mode

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
        self.assertEqual(menu.state.notice,'status.target_accepted')

if __name__=='__main__':
    unittest.main()
