"""Player controller: playback lifecycle and Sonos ownership (simulated Sonos)."""
import queue
import unittest
from types import SimpleNamespace
from sonoscdplayer.player.controller import Controller, wait_startup_pcm, TrackServerProcess
from sonoscdplayer.player.state import PlayerState, Mode
from sonoscdplayer.player.service import handle_command



class FakeServer:
    def __init__(self): self.up=False; self.starts=[]
    def alive(self): return self.up
    def start(self, track, **options): self.starts.append(track); self.options=options; self.up=True
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
        self.c.apply_command('next'); self.assertEqual(self.c.state.selected_track,2); self.assertEqual(self.p.plays,[])
    def test_start_confirmation(self):
        self.c.apply_command('play'); self.assertEqual(self.c.state.mode,Mode.STARTING)
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


class Server:
    def __init__(self): self.up=False; self.starts=[]
    def alive(self): return self.up
    def start(self, track, **options): self.starts.append(track); self.options=options; self.up=True
    def close(self): self.up=False


class LeaderGroup:
    def __init__(self,p): self.members=[p]; self.coordinator=p


class Speaker:
    def __init__(self, uid, ip):
        self.uid=uid; self.ip_address=ip; self.group=LeaderGroup(self)
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


class Tests(unittest.TestCase):
    def test_ready(self):
        q=queue.Queue(); q.put((0,'[+ 1.0s] STARTUP_PCM_READY bytes=58800'))
        wait_startup_pcm(q, SimpleNamespace(poll=lambda:None), timeout=.05)
    def test_reader_end_fails(self):
        q=queue.Queue(); q.put((0,'READER end written=0/100 error=IOError'))
        with self.assertRaisesRegex(RuntimeError, 'ended'):
            wait_startup_pcm(q, SimpleNamespace(poll=lambda:None), timeout=.05)
    def test_exit_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'exited'):
            wait_startup_pcm(queue.Queue(), SimpleNamespace(poll=lambda:1), timeout=.05)
    def test_timeout_fails_closed(self):
        with self.assertRaisesRegex(TimeoutError, 'Sonos PLAY not sent'):
            wait_startup_pcm(queue.Queue(), SimpleNamespace(poll=lambda:None), timeout=.02)
    def test_server_default_enabled(self):
        p=TrackServerProcess('.', '/dev/sr0', 'config.toml', 8765)
        self.assertTrue(p.startup_prebuffer)
        self.assertEqual(p.startup_timeout,45)


if __name__ == '__main__':
    unittest.main()
