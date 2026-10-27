"""M0.12b: structured read-only status and wire compatibility."""
import json
import unittest
from player_service import handle_command
from player_controller import Controller
from player_state import PlayerState


class Server:
    def __init__(self): self.up = False
    def alive(self): return self.up
    def start(self, track): self.up = True
    def close(self): self.up = False


class Target:
    uid = 'TEST_SPEAKER'
    ip_address = '198.51.100.22'
    def __init__(self):
        self.transport = 'STOPPED'
        self.uri = ''
        self.plays = 0
        self.stops = 0
        self.group = type('Group', (), {'members': [self], 'coordinator': self})()
    def play_uri(self, uri, title):
        self.plays += 1
        self.uri = uri
        self.transport = 'TRANSITIONING'
    def get_current_transport_info(self):
        return {'current_transport_state': self.transport}
    def get_current_track_info(self):
        return {'uri': self.uri}
    def stop(self): self.stops += 1


class StatusAPITests(unittest.TestCase):
    def setUp(self):
        self.target = Target()
        self.server = Server()
        self.verify_calls = 0
        def verify(uid):
            self.verify_calls += 1
            self.assertEqual(uid, 'TEST_SPEAKER')
            return self.target
        self.ctrl = Controller(PlayerState().disc_loaded((1, 2, 3)), None, None,
                               8765, self.server, verify=verify,
                               route=lambda ip: '192.0.2.44')

    def test_stopped_json_and_legacy_status(self):
        reply = handle_command(self.ctrl, {'command':'status'})
        self.assertTrue(reply['ok'])
        self.assertIn('mode=STOPPED track=1/3', reply['status'])
        self.assertEqual(reply['state'], {
            'schema_version':1,
            'player':{'mode':'STOPPED','track':1,'track_count':3,'error':None},
            'sonos':{'target_uid':None,'transport':'UNKNOWN','owned':False},
            'stream':{'active':False},
        })
        self.assertEqual(self.verify_calls, 0)
        json.dumps(reply)

    def test_status_never_calls_sonos_or_stream_start(self):
        for _ in range(50):
            handle_command(self.ctrl, {'command':'status'})
        self.assertEqual(self.verify_calls, 0)
        self.assertEqual(self.target.plays, 0)
        self.assertFalse(self.server.up)

    def test_play_and_stop_status_lifecycle(self):
        result = handle_command(self.ctrl, {'command':'play','uid':'TEST_SPEAKER'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['state']['player']['mode'], 'STARTING')
        self.assertEqual(result['state']['sonos']['target_uid'], 'TEST_SPEAKER')
        self.assertTrue(result['state']['stream']['active'])
        self.assertEqual(self.target.plays, 1)
        self.target.transport = 'PLAYING'
        self.ctrl.refresh()
        result = handle_command(self.ctrl, {'command':'status'})
        self.assertEqual(result['state']['player']['mode'], 'PLAYING')
        self.assertEqual(result['state']['sonos']['transport'], 'PLAYING')
        self.assertEqual(self.target.plays, 1)
        # Simulate external natural end (no destructive command).
        self.target.transport = 'STOPPED'
        self.ctrl.refresh()
        result = handle_command(self.ctrl, {'command':'status'})
        self.assertEqual(result['state']['player']['mode'], 'STOPPED')
        self.assertIsNone(result['state']['sonos']['target_uid'])
        self.assertFalse(result['state']['stream']['active'])
        self.assertEqual(self.target.stops, 0)

    def test_error_reply_retains_structured_and_legacy_fields(self):
        result = handle_command(self.ctrl, {'command':'play'})
        self.assertFalse(result['ok'])
        self.assertIn('status', result)
        self.assertEqual(result['state']['schema_version'], 1)
        self.assertEqual(result['state']['player']['mode'], 'STOPPED')

    def test_select_changes_snapshot_without_sonos(self):
        reply = handle_command(self.ctrl, {'command':'select 3'})
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['state']['player']['track'], 3)
        self.assertEqual(self.verify_calls, 0)

if __name__ == '__main__': unittest.main()
