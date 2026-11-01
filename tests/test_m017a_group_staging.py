"""Offline regression checks for staged multi-speaker target selection."""
import unittest
from types import SimpleNamespace as NS
from player_service import handle_command
from player_state import Mode
from player_target_selection import SelectedTarget
from sonos_group import normalize_members, is_expected_group, release, unresolved_members
from sonos_ui.selection_plan import plan_from_applied

class FakeController:
    state=NS(mode=Mode.STOPPED)
    owned_url=None
    def status(self): return 'stopped'
    def status_snapshot(self): return {'schema_version':1}

class Speaker:
    def __init__(self, uid, group=None):
        self.uid=uid
        self._group=group
        self.transport='STOPPED'
        self.unjoins=0
        self.raise_unjoin=False
    @property
    def group(self): return self._group
    def get_current_transport_info(self): return {'current_transport_state':self.transport}
    def get_current_track_info(self): return {'uri':'own'}
    def unjoin(self):
        if self.raise_unjoin: raise OSError('temporary failure')
        self.unjoins+=1
        self._group=NS(coordinator=self,members=(self,))

class StagedGroupTests(unittest.TestCase):
    def test_group_intent_from_solo_checkboxes(self):
        a=NS(key='A',members=('A',))
        b=NS(key='B',members=('B',))
        self.assertEqual(plan_from_applied((a,b),{'A','B'}).members,('B',))

    def test_existing_group_not_planned(self):
        a=NS(key='A',members=('A','B'))
        with self.assertRaises(ValueError): plan_from_applied((a,),{'A'})

    def test_set_target_preflights_all_members_without_join(self):
        member=Speaker('B')
        member._group=NS(coordinator=member,members=(member,))
        checked=[]
        sel=SelectedTarget(verify=lambda uid:checked.append(uid),
                           find_many=lambda uids:{'B':member})
        reply=handle_command(FakeController(),{'command':'set-target','uid':'A','members':['B']},sel)
        self.assertTrue(reply['ok'],reply)
        self.assertEqual(checked,['A'])
        self.assertEqual(sel.members_for_explicit_action(),('B',))
        self.assertNotIn('B',str(reply))
        self.assertEqual(member.unjoins,0)

    def test_play_with_members_is_forbidden(self):
        sel=SelectedTarget(verify=lambda uid:True)
        reply=handle_command(FakeController(),{'command':'play','uid':'A','members':['B']},sel)
        self.assertFalse(reply['ok'])
        self.assertIsNone(sel.uid_for_explicit_action())

    def test_grouped_member_rejected_without_mutation(self):
        member=Speaker('B')
        coord=Speaker('Z')
        member._group=NS(coordinator=coord,members=(coord,member))
        sel=SelectedTarget(verify=lambda uid:True,find_many=lambda uids:{'B':member})
        reply=handle_command(FakeController(),{'command':'set-target','uid':'A','members':['B']},sel)
        self.assertFalse(reply['ok'])
        self.assertIsNone(sel.uid_for_explicit_action())

    def test_uncertain_release_remains_tracked(self):
        a,b=Speaker('A'),Speaker('B')
        group=NS(coordinator=a,members=(a,b))
        a._group=b._group=group
        b.raise_unjoin=True
        result=release(a,{'B':b},owned_url='own')
        self.assertIn('B',unresolved_members({'B':b},result))
        self.assertEqual(b.unjoins,0)

    def test_foreign_expansion_prevents_release(self):
        a,b,c=Speaker('A'),Speaker('B'),Speaker('C')
        group=NS(coordinator=a,members=(a,b,c))
        a._group=b._group=c._group=group
        result=release(a,{'B':b},owned_url='own')
        self.assertEqual(result['B'],'kept: topology changed')
        self.assertEqual(b.unjoins,0)

if __name__=='__main__':
    unittest.main()
