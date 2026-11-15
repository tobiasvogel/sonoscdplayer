import unittest
from src.integrations.outputs.oled_status import wait_for_player_ready

class TestSplashReadiness(unittest.TestCase):
    def test_immediate_valid_state(self):
        calls=[]
        def status(path):
            calls.append(path)
            return {'player':{'mode':'STOPPED'}}
        self.assertEqual(wait_for_player_ready(status,'/test'), {'player':{'mode':'STOPPED'}})
        self.assertEqual(calls,['/test'])

    def test_retries_until_service_ready(self):
        calls=[0]
        def status(path):
            calls[0]+=1
            if calls[0] < 3: raise ConnectionRefusedError()
            return {'player':{'mode':'STARTING'}}
        clock=[0.0]
        def sleep(t): clock[0]+=t
        self.assertEqual(wait_for_player_ready(status,'/test',clock=lambda:clock[0],sleep=sleep)['player']['mode'],'STARTING')
        self.assertEqual(calls[0],3)

    def test_offline_timeout(self):
        clock=[0.0]
        def sleep(t): clock[0]+=t
        def offline(path): raise FileNotFoundError()
        self.assertIsNone(wait_for_player_ready(offline,'/test',max_wait_seconds=1.0,clock=lambda:clock[0],sleep=sleep))
        self.assertEqual(clock[0],1.0)

    def test_invalid_snapshots_do_not_signal_ready(self):
        calls=[0]
        def status(path):
            calls[0]+=1
            return {'player': {'mode': 10}} if calls[0]==1 else {'player':{'mode':'ERROR'}}
        self.assertEqual(wait_for_player_ready(status,'/test',sleep=lambda _:None)['player']['mode'],'ERROR')
