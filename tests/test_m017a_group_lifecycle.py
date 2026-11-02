"""Simulated controller lifecycle for explicit temporary groups."""
import contextlib
import io
import unittest
from player_controller import Controller
from player_state import PlayerState, Mode

class Topology:
    def __init__(self):
        self.leaders = {'A':'A','B':'B','C':'C'}
        self.devices = {uid: Speaker(uid,self) for uid in self.leaders}

class Speaker:
    def __init__(self, uid, topology):
        self.uid=uid
        self.topology=topology
        self.ip_address='192.0.2.20'
        self.transport='STOPPED'
        self.uri=''
        self.stop_calls=0
        self.unjoin_calls=0
        self.play_calls=0
    @property
    def group(self):
        leader=self.topology.leaders[self.uid]
        coord=self.topology.devices[leader]
        members=[self.topology.devices[u] for u,v in self.topology.leaders.items() if v==leader]
        return type('Group',(),{'coordinator':coord,'members':members})()
    def get_current_transport_info(self):
        return {'current_transport_state':self.transport}
    def get_current_track_info(self):
        return {'uri':self.uri}
    def join(self,coordinator):
        self.topology.leaders[self.uid]=coordinator.uid
    def unjoin(self):
        self.unjoin_calls+=1
        self.topology.leaders[self.uid]=self.uid
    def play_uri(self,url,title):
        self.play_calls+=1
        self.uri=url
        self.transport='TRANSITIONING'
    def stop(self):
        self.stop_calls+=1
        self.transport='STOPPED'
    def pause(self): self.transport='PAUSED_PLAYBACK'
    def play(self): self.transport='PLAYING'

class Server:
    def __init__(self): self.running=False
    def alive(self): return self.running
    def start(self,track): self.running=True
    def close(self): self.running=False

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.topo=Topology()
        self.a,self.b,self.c=(self.topo.devices[u] for u in ('A','B','C'))
        self.server=Server()
        def verify(uid):
            p=self.topo.devices[uid]
            if p.group.coordinator.uid!=uid or len(p.group.members)!=1 or p.transport!='STOPPED':
                raise RuntimeError('not stopped solo')
            return p
        self.player=Controller(PlayerState().disc_loaded((1,2,3)),
                               'A','192.0.2.10',8765,self.server,
                               verify=verify,
                               find_many=lambda uids:{uid:self.topo.devices[uid] for uid in uids})
    def start(self):
        self.player.play('A',['B'])
        self.a.transport='PLAYING'
        self.player.refresh()
    def test_explicit_group_only_coordinator_plays(self):
        self.start()
        self.assertEqual((self.a.play_calls,self.b.play_calls),(1,0))
        self.assertEqual(self.topo.leaders['B'],'A')
    def test_stop_releases_only_owned_group(self):
        self.start()
        self.player.stop()
        self.assertEqual(self.topo.leaders['B'],'B')
        self.assertEqual((self.a.stop_calls,self.b.unjoin_calls),(1,1))
    def test_external_group_change_blocks_stop(self):
        self.start()
        self.topo.leaders['C']='A'
        with self.assertRaises(RuntimeError):
            self.player.stop()
        self.assertEqual((self.a.stop_calls,self.b.unjoin_calls),(0,0))
    def test_track_switch_keeps_group(self):
        self.start()
        self.player.change('next')
        self.assertEqual(self.topo.leaders['B'],'A')
        self.assertEqual(self.b.unjoin_calls,0)
        self.assertEqual(self.a.play_calls,2)
    def test_failed_play_uri_leaves_group_for_manual_recovery(self):
        def fail(url,title):
            raise OSError('ambiguous send')
        self.a.play_uri=fail
        with self.assertRaises(OSError):
            self.player.play('A',['B'])
        self.assertEqual(self.topo.leaders['B'],'A')
        self.assertTrue(self.player._group_uncertain)
        self.assertEqual(self.b.unjoin_calls,0)
        with self.assertRaises(RuntimeError):
            self.player.reset()
    def test_solo_play_unchanged(self):
        self.player.play('A')
        self.assertEqual(self.topo.leaders,{'A':'A','B':'B','C':'C'})

if __name__=='__main__':
    unittest.main()
