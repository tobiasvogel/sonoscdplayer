import unittest
from player_controller import Controller
from player_state import PlayerState, Mode

class FakeServer:
    def __init__(self): self.up=False; self.starts=[]
    def alive(self): return self.up
    def start(self, track): self.starts.append(track); self.up=True
    def close(self): self.up=False

class Group:
    def __init__(self, player): self.members=[player]; self.coordinator=player

class FakePlayer:
    uid='TEST_UID'
    def __init__(self): self.group=Group(self); self.transport='STOPPED'; self.uri=''; self.stops=0; self.plays=[]
    def get_current_transport_info(self): return {'current_transport_state':self.transport}
    def get_current_track_info(self): return {'uri':self.uri}
    def play_uri(self, url, title): self.uri=url; self.transport='TRANSITIONING'; self.plays.append(url)
    def stop(self): self.stops+=1; self.transport='STOPPED'

class TestController(unittest.TestCase):
    def setUp(self):
        self.p=FakePlayer(); self.s=FakeServer()
        def verify(_):
            if self.p.transport != 'STOPPED': raise RuntimeError('not STOPPED')
            return self.p
        self.c=Controller(PlayerState().disc_loaded((1,2,3)), 'TEST_UID', '192.0.2.10',8765,self.s,verify)
    def playing(self):
        self.c.play(); self.p.transport='PLAYING'; self.c.refresh()
        self.assertEqual(self.c.state.mode,Mode.PLAYING)
    def test_no_sonos_action_before_play(self):
        self.c.command('next'); self.assertEqual(self.c.state.selected_track,2); self.assertEqual(self.p.plays,[])
    def test_start_confirmation(self):
        self.c.command('play'); self.assertEqual(self.c.state.mode,Mode.STARTING)
        self.c.refresh(); self.assertEqual(self.c.state.mode,Mode.STARTING)
        self.p.transport='PLAYING'; self.c.refresh(); self.assertEqual(self.c.state.mode,Mode.PLAYING)
    def test_switch_next(self):
        self.playing(); self.c.change('next'); self.assertEqual(self.p.stops,1)
        self.assertEqual(self.s.starts,[1,2]); self.assertEqual(self.c.state.mode,Mode.STARTING)
    def test_switch_previous(self):
        self.c.change('next'); self.playing(); self.c.change('previous'); self.assertEqual(self.s.starts,[2,1])
    def test_refuse_stop_if_uri_changed(self):
        self.playing(); self.p.uri='http://external/';
        with self.assertRaises(RuntimeError): self.c.stop()
        self.assertEqual(self.p.stops,0)
    def test_refuse_stop_if_group_changed(self):
        self.playing(); self.p.group.members.append(FakePlayer())
        with self.assertRaises(RuntimeError): self.c.stop()
        self.assertEqual(self.p.stops,0)
    def test_explicit_owned_stop(self):
        self.playing(); self.c.stop(); self.assertEqual(self.p.stops,1)
        self.assertEqual(self.c.state.mode,Mode.STOPPED)
    def test_close_no_sonos_stop(self):
        self.playing(); self.c.close(); self.assertEqual(self.p.stops,0)
    def test_natural_end(self):
        self.playing(); self.p.transport='STOPPED'; self.c.refresh()
        self.assertEqual(self.c.state.mode,Mode.STOPPED); self.assertFalse(self.s.alive())
    def test_refuse_switch_during_starting(self):
        self.c.play()
        with self.assertRaises(ValueError): self.c.change('next')
        self.assertEqual(self.p.stops,0)
    def test_boundary_no_stop(self):
        self.c.state=self.c.state.select(3); self.playing(); self.c.change('next')
        self.assertEqual(self.p.stops,0)
    def test_play_failure_is_error_not_starting(self):
        def fail(*args, **kwargs): raise OSError('Sonos refused')
        self.p.play_uri=fail
        with self.assertRaises(OSError): self.c.play()
        self.assertEqual(self.c.state.mode,Mode.ERROR)
        self.assertFalse(self.s.alive())
    def test_external_takeover_fails_closed(self):
        self.playing(); self.p.uri='http://external/'; self.c.refresh()
        self.assertEqual(self.c.state.mode,Mode.ERROR); self.assertEqual(self.p.stops,0)

if __name__=='__main__':unittest.main()
