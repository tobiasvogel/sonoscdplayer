import unittest
from player_controller import Controller
from player_service import handle_command
from player_state import PlayerState, Mode
from test_m012b import Target, Server

class PausableTarget(Target):
    def __init__(self):
        super().__init__(); self.pauses=0; self.resumes=0
    def pause(self):
        self.pauses+=1;self.transport='PAUSED_PLAYBACK'
    def play(self):
        self.resumes+=1;self.transport='PLAYING'
    def stop(self):
        super().stop();self.transport='STOPPED'

class PauseTests(unittest.TestCase):
    def setUp(self):
        self.target=PausableTarget();self.server=Server()
        self.ctrl=Controller(PlayerState().disc_loaded((1,2)),None,None,8765,self.server,verify=lambda uid:self.target,route=lambda ip:'192.0.2.44')
    def playing(self):
        self.assertTrue(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
        self.target.transport='PLAYING';self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.PLAYING)
    def paused(self):
        self.playing();self.assertTrue(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
        self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
    def test_full_toggle_and_stop(self):
        self.paused();self.assertTrue(self.server.alive());self.assertEqual(self.target.pauses,1)
        self.assertTrue(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
        self.assertEqual(self.ctrl.state.mode,Mode.PLAYING);self.assertEqual(self.target.resumes,1)
        self.ctrl.stop();self.assertEqual(self.target.stops,1)
    def test_changed_uri_blocks_resume_and_stop(self):
        self.paused();self.target.uri='foreign'
        self.assertFalse(handle_command(self.ctrl,{'command':'resume'})['ok']);self.assertEqual(self.target.resumes,0)
        self.assertFalse(handle_command(self.ctrl,{'command':'stop'})['ok']);self.assertEqual(self.target.stops,0)
    def test_changed_group_blocks_pause(self):
        self.playing();self.target.group.members.append(PausableTarget())
        self.assertFalse(handle_command(self.ctrl,{'command':'pause'})['ok']);self.assertEqual(self.target.pauses,0)
    def test_pause_status_no_server_close(self):
        self.paused();r=handle_command(self.ctrl,{'command':'status'})
        self.assertEqual(r['state']['player']['mode'],'PAUSED');self.assertTrue(r['state']['stream']['active'])
        self.ctrl.refresh();self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
    def test_external_resume_observed(self):
        self.paused();self.target.transport='PLAYING';self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.PLAYING)
    def test_paused_shutdown_requires_exact_uri(self):
        self.paused();self.assertEqual(self.ctrl.shutdown_owned()['action'],'stopped_owned')
        self.assertEqual(self.target.stops,1)
    def test_paused_shutdown_denies_foreign(self):
        self.paused();self.target.uri='other'
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'not_stopped');self.assertEqual(self.target.stops,0)
    def test_starting_toggle_denied(self):
        self.assertTrue(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
        self.assertFalse(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
    def test_uid_mismatch_denied(self):
        self.paused();self.assertFalse(handle_command(self.ctrl,{'command':'playpause','uid':'OTHER'})['ok'])
        self.assertEqual(self.target.resumes,0)
    def test_service_requires_uid_on_toggle(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'playpause'})['ok'])
    def test_no_pause_on_stopped(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'pause'})['ok'])
