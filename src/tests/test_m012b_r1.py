"""M0.12b-r1: incomplete Sonos startup and explicit emergency stop."""
import unittest
from unittest.mock import patch
from player_controller import Controller
from player_service import handle_command
from player_state import PlayerState
from test_m012b import Target, Server

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.target=Target();self.server=Server()
        self.ctrl=Controller(PlayerState().disc_loaded((1,2,3)),None,None,8765,self.server,
                             verify=lambda uid:self.target,route=lambda ip:'192.0.2.44')

    def test_starting_stopped_can_be_released_without_sonos_stop(self):
        self.assertTrue(handle_command(self.ctrl,{'command':'play','uid':'TEST_SPEAKER'})['ok'])
        self.target.transport='STOPPED'
        result=handle_command(self.ctrl,{'command':'stop'})
        self.assertTrue(result['ok']);self.assertEqual(self.target.stops,0)
        self.assertEqual(result['state']['player']['mode'],'STOPPED')
        self.assertFalse(self.server.alive())

    def test_starting_transitional_stop_is_refused(self):
        self.ctrl.play('TEST_SPEAKER')
        self.assertFalse(handle_command(self.ctrl,{'command':'stop'})['ok'])
        self.assertEqual(self.target.stops,0)

    def test_stopped_start_timeout_closes_local_only(self):
        self.ctrl.play('TEST_SPEAKER')
        self.target.transport='STOPPED'
        with patch('player_controller.time.monotonic',return_value=self.ctrl.start_monotonic+21):
            self.ctrl.refresh()
        with patch('player_controller.time.monotonic',return_value=self.ctrl.start_monotonic+27):
            self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode.value,'ERROR')
        self.assertFalse(self.server.alive())
        self.assertEqual(self.target.stops,0)

    def test_force_stop_requires_explicit_uid_and_solo(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'stop','force':True})['ok'])
        self.assertFalse(handle_command(self.ctrl,{'command':'next','uid':'TEST_SPEAKER','force':True})['ok'])
        self.assertEqual(self.target.stops,0)
        with patch('sonos_parallel_test.find_target',return_value=self.target):
            r=handle_command(self.ctrl,{'command':'stop','uid':'TEST_SPEAKER','force':True})
        self.assertTrue(r['ok']);self.assertEqual(self.target.stops,1)

    def test_force_stop_clears_detached_error(self):
        self.ctrl.play('TEST_SPEAKER')
        self.ctrl._fail('start aborted')
        self.assertEqual(self.ctrl.state.mode.value,'ERROR')
        with patch('sonos_parallel_test.find_target',return_value=self.target):
            r=handle_command(self.ctrl,{'command':'stop','uid':'TEST_SPEAKER','force':True})
        self.assertTrue(r['ok'])
        self.assertEqual(r['state']['player']['mode'],'STOPPED')
        self.assertIsNone(r['state']['player']['error'])
        self.assertEqual(self.target.stops,1)
        self.assertTrue(handle_command(self.ctrl,{'command':'play','uid':'TEST_SPEAKER'})['ok'])

    def test_reset_is_local_only_after_error(self):
        self.ctrl.play('TEST_SPEAKER')
        self.ctrl._fail('start aborted')
        r=handle_command(self.ctrl,{'command':'reset'})
        self.assertTrue(r['ok'])
        self.assertEqual(r['state']['player']['mode'],'STOPPED')
        self.assertEqual(self.target.stops,0)

    def test_reset_refuses_active_and_stopped_state(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'reset'})['ok'])
        self.ctrl.play('TEST_SPEAKER')
        self.assertFalse(handle_command(self.ctrl,{'command':'reset'})['ok'])
        self.assertEqual(self.target.stops,0)

    def test_force_stop_refuses_group(self):
        other=Target();self.target.group.members.append(other)
        with patch('sonos_parallel_test.find_target',return_value=self.target):
            r=handle_command(self.ctrl,{'command':'stop','uid':'TEST_SPEAKER','force':True})
        self.assertFalse(r['ok']);self.assertEqual(self.target.stops,0)

    def test_force_stop_refuses_other_uid_during_active_session(self):
        self.ctrl.play('TEST_SPEAKER')
        r=handle_command(self.ctrl,{'command':'stop','uid':'ANOTHER_SPEAKER','force':True})
        self.assertFalse(r['ok']);self.assertEqual(self.target.stops,0)

if __name__=='__main__':unittest.main()
