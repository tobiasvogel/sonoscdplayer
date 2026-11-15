import unittest
from player_controller import Controller
from player_state import PlayerState, Mode
from player_service import handle_command

class Server:
    def __init__(self): self.up=False; self.starts=[]
    def alive(self): return self.up
    def start(self, track): self.starts.append(track); self.up=True
    def close(self): self.up=False
class Group:
    def __init__(self,p): self.members=[p]; self.coordinator=p
class Speaker:
    def __init__(self, uid, ip):
        self.uid=uid; self.ip_address=ip; self.group=Group(self)
        self.transport='STOPPED'; self.uri=''; self.plays=[]; self.stops=0
    def get_current_transport_info(self): return {'current_transport_state': self.transport}
    def get_current_track_info(self): return {'uri':self.uri}
    def play_uri(self,url,title): self.uri=url; self.plays.append(url); self.transport='TRANSITIONING'
    def stop(self): self.stops+=1; self.transport='STOPPED'

class TargetSelectionTests(unittest.TestCase):
    def setUp(self):
        self.one=Speaker('TEST_ONE','198.51.100.10'); self.two=Speaker('TEST_TWO','198.51.100.20')
        self.speakers={p.uid:p for p in (self.one,self.two)}
        def verify(uid):
            p=self.speakers[uid]
            if p.transport!='STOPPED': raise ValueError('not stopped')
            return p
        self.server=Server()
        self.ctrl=Controller(PlayerState().disc_loaded((1,2,3)), None, None, 8765,
                             self.server,verify,route=lambda ip: '192.0.2.10' if ip.endswith('.10') else '192.0.2.20')
    def test_remote_select_then_play_chooses_uid_and_route(self):
        self.assertTrue(handle_command(self.ctrl, {'command':'select 3'})['ok'])
        self.assertTrue(handle_command(self.ctrl, {'command':'play','uid':'TEST_ONE'})['ok'])
        self.assertEqual(self.ctrl.uid,'TEST_ONE')
        self.assertEqual(self.one.plays,['http://192.0.2.10:8765/track/3.wav'])
        self.assertEqual(self.two.plays,[])
    def test_no_takeover_during_owned_playback(self):
        self.ctrl.play('TEST_ONE'); self.one.transport='PLAYING'; self.ctrl.refresh()
        result=handle_command(self.ctrl, {'command':'play','uid':'TEST_TWO'})
        self.assertFalse(result['ok']); self.assertEqual(self.two.plays,[])
        self.assertEqual(self.one.stops,0)
    def test_select_new_target_after_owned_stop(self):
        self.ctrl.play('TEST_ONE'); self.one.transport='PLAYING'; self.ctrl.refresh()
        self.ctrl.stop()
        self.ctrl.play('TEST_TWO')
        self.assertEqual(self.two.plays,['http://192.0.2.20:8765/track/1.wav'])
    def test_route_failure_refuses_stream(self):
        self.ctrl.route=lambda ip: (_ for _ in ()).throw(OSError('no route'))
        result=handle_command(self.ctrl,{'command':'play','uid':'TEST_ONE'})
        self.assertFalse(result['ok']); self.assertEqual(self.one.plays,[])
        self.assertFalse(self.server.up)

if __name__=='__main__': unittest.main()
