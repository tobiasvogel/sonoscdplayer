"""M0.12c-r7: pause/resume lifecycle regression tests (no physical Sonos)."""
import unittest
from player_state import Mode, PlayerState
from player_controller import Controller
from player_service import handle_command
from test_m012c_r3 import PausableTarget
from test_m012b import Server


class CountingServer(Server):
    def __init__(self):
        super().__init__(); self.starts=0; self.closes=0
    def start(self, track):
        self.starts += 1; super().start(track)
    def close(self):
        self.closes += 1; super().close()


class PauseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.target=PausableTarget(); self.server=CountingServer()
        self.ctrl=Controller(PlayerState().disc_loaded((1,2,3)), None,None,8765,self.server,
                             verify=lambda uid:self.target, route=lambda ip:'192.0.2.44')
        self.server.closes=0  # ignore constructor cleanup
    def send(self, command, uid=False):
        r={'command':command}
        if uid: r['uid']='TEST_SPEAKER'
        return handle_command(self.ctrl,r)
    def playing(self):
        self.assertTrue(self.send('playpause',True)['ok'])
        self.target.transport='PLAYING'; self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.PLAYING)
    def paused(self):
        self.playing(); self.assertTrue(self.send('pause')['ok'])
        self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
    def test_multiple_pause_resume_cycles_keep_same_stream(self):
        self.playing(); url=self.ctrl.owned_url
        for _ in range(5):
            self.assertTrue(self.send('playpause',True)['ok'])
            self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
            self.ctrl.refresh()
            self.assertTrue(self.send('playpause',True)['ok'])
            self.ctrl.refresh()
            self.assertEqual(self.ctrl.state.mode,Mode.PLAYING)
            self.assertEqual(self.ctrl.owned_url,url)
        self.assertEqual(self.server.starts,1)
        self.assertEqual(self.server.closes,1)
        self.assertEqual(self.target.pauses,5)
        self.assertEqual(self.target.resumes,5)
    def test_long_pause_refresh_does_not_expire_session(self):
        self.paused()
        for _ in range(90): self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
        self.assertTrue(self.send('resume')['ok'])
        self.assertEqual(self.server.starts,1)
    def test_duplicate_pause_and_resume_rejected(self):
        self.paused(); self.assertFalse(self.send('pause')['ok'])
        self.assertEqual(self.target.pauses,1)
        self.assertTrue(self.send('resume')['ok'])
        self.assertFalse(self.send('resume')['ok'])
        self.assertEqual(self.target.resumes,1)
    def test_stop_from_pause_cleans_only_own_session(self):
        self.paused(); self.assertTrue(self.send('stop')['ok'])
        self.assertEqual(self.ctrl.state.mode,Mode.STOPPED)
        self.assertEqual(self.target.stops,1)
        self.assertEqual(self.server.closes,2)
        self.assertFalse(self.send('resume')['ok'])
    def test_next_from_paused_stops_owned_then_starts_new_track(self):
        self.paused(); old=self.ctrl.owned_url
        self.assertTrue(self.send('next')['ok'])
        self.assertEqual(self.ctrl.state.selected_track,2)
        self.assertEqual(self.ctrl.state.mode,Mode.STARTING)
        self.assertNotEqual(self.ctrl.owned_url,old)
        self.assertEqual(self.target.stops,1)
        self.assertEqual(self.server.starts,2)
    def test_previous_from_paused_starts_previous(self):
        self.assertTrue(self.send('select 2')['ok'])
        self.paused(); self.assertTrue(self.send('previous')['ok'])
        self.assertEqual(self.ctrl.state.selected_track,1)
        self.assertEqual(self.target.stops,1)
    def test_foreign_uri_blocks_paused_next(self):
        self.paused(); self.target.uri='http://example.invalid/foreign.wav'
        self.assertFalse(self.send('next')['ok'])
        self.assertEqual(self.target.stops,0)
        self.assertEqual(self.server.starts,1)
    def test_foreign_group_blocks_paused_stop(self):
        self.paused(); self.target.group.members.append(PausableTarget())
        self.assertFalse(self.send('stop')['ok'])
        self.assertEqual(self.target.stops,0)
    def test_external_stop_during_pause_clears_session_without_stop_command(self):
        self.paused(); self.target.transport='STOPPED'; self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.STOPPED)
        self.assertFalse(self.server.alive())
        self.assertEqual(self.target.stops,0)
        self.assertFalse(self.send('resume')['ok'])
    def test_reader_server_failure_during_pause_is_error(self):
        self.paused(); self.server.up=False; self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.ERROR)
        self.assertFalse(self.send('resume')['ok'])

if __name__=='__main__': unittest.main()
