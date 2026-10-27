"""M0.12b-r7: fail-closed service exit and read-only remote Sonos status."""
import unittest
from unittest.mock import patch
from player_controller import Controller
from player_service import handle_command
from player_state import PlayerState
from test_m012b import Target, Server

class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.target, self.server = Target(), Server()
        self.ctrl = Controller(PlayerState().disc_loaded((1,2,3)),None,None,8765,self.server,
                               verify=lambda uid:self.target, route=lambda ip:'192.0.2.44')
    def playing(self):
        self.ctrl.play('TEST_SPEAKER')
        self.target.transport='PLAYING'
        self.ctrl.refresh()
    def test_owned_playing_is_stopped(self):
        self.playing()
        result=self.ctrl.shutdown_owned()
        self.assertEqual(result['action'],'stopped_owned')
        self.assertEqual(self.target.stops,1)
        self.ctrl.close()
        self.assertFalse(self.server.alive())
    def test_starting_never_force_stops(self):
        self.ctrl.play('TEST_SPEAKER')
        result=self.ctrl.shutdown_owned()
        self.assertEqual(result['action'],'not_stopped')
        self.assertEqual(self.target.stops,0)
        self.ctrl.close()
    def test_changed_uri_no_stop(self):
        self.playing()
        self.target.uri='http://example.invalid/foreign.wav'
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'not_stopped')
        self.assertEqual(self.target.stops,0)
    def test_group_changed_no_stop(self):
        self.playing()
        self.target.group.members.append(Target())
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'not_stopped')
        self.assertEqual(self.target.stops,0)
    def test_transport_stopped_no_stop(self):
        self.playing()
        self.target.transport='STOPPED'
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'not_stopped')
        self.assertEqual(self.target.stops,0)
    def test_leave_playing_never_stops(self):
        self.playing()
        self.assertEqual(self.ctrl.shutdown_owned('leave_playing')['action'],'left_playing')
        self.assertEqual(self.target.stops,0)
    def test_live_read_only_does_not_attach(self):
        self.playing()
        with patch('sonos_parallel_test.find_target', return_value=self.target):
            reply=handle_command(self.ctrl,{'command':'sonos-status','uid':'TEST_SPEAKER'})
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['live_sonos']['transport'],'PLAYING')
        self.assertTrue(reply['live_sonos']['solo'])
        self.assertEqual(self.target.stops,0)
        self.assertEqual(self.target.plays,1)
    def test_live_status_requires_explicit_uid(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'sonos-status'})['ok'])
    def test_shutdown_twice_never_stops_twice(self):
        self.playing()
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'stopped_owned')
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'nothing_to_stop')
        self.assertEqual(self.target.stops,1)

if __name__=='__main__': unittest.main()
