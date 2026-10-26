import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from player_switch import ownership_snapshot, stopped_snapshot, switch_owned

UID = 'TEST_TARGET_UID'
OLD = 'http://192.0.2.12:8765/track/2.wav'
NEW = 'http://192.0.2.12:8765/track/3.wav'

def fake(transport='PLAYING', uri=OLD, solo=True, coordinator=True):
    p = Mock()
    p.uid = UID
    p.group = SimpleNamespace(members=[p] if solo else [p, Mock(uid='OTHER')],
                              coordinator=p if coordinator else Mock(uid='OTHER'))
    p.get_current_transport_info.return_value = {'current_transport_state': transport}
    p.get_current_track_info.return_value = {'uri': uri}
    return p

class OwnershipTests(unittest.TestCase):
    def test_owned(self): ownership_snapshot(fake(), UID, OLD)
    def test_rejects_wrong_uri(self):
        for uri in ('', NEW, 'x-rincon:other', None):
            with self.subTest(uri=uri), self.assertRaises(RuntimeError): ownership_snapshot(fake(uri=uri), UID, OLD)
    def test_rejects_not_playing(self):
        for status in ('STOPPED', 'TRANSITIONING', 'PAUSED_PLAYBACK'):
            with self.subTest(status=status), self.assertRaises(RuntimeError): ownership_snapshot(fake(transport=status), UID, OLD)
    def test_rejects_group_or_coordinator(self):
        for p in (fake(solo=False), fake(coordinator=False)):
            with self.assertRaises(RuntimeError): ownership_snapshot(p, UID, OLD)
    def test_rejects_wrong_uid(self):
        with self.assertRaises(RuntimeError): ownership_snapshot(fake(), 'WRONG', OLD)
    def test_stopped_snapshot(self):
        stopped_snapshot(fake(transport='STOPPED'), UID)
        with self.assertRaises(RuntimeError): stopped_snapshot(fake(), UID)
    def test_switch_order_and_guard(self):
        p = fake()
        transitions = []
        def stop():
            transitions.append('STOP')
            p.get_current_transport_info.return_value = {'current_transport_state':'STOPPED'}
        def play(url, title):
            transitions.append(('PLAY',url))
        switch_owned(p, UID, OLD, NEW, stop=stop, play=play)
        self.assertEqual(transitions, ['STOP',('PLAY',NEW)])
        self.assertEqual(p.get_current_track_info.call_count, 2)
    def test_never_stop_wrong_source(self):
        p = fake(uri=NEW)
        with self.assertRaises(RuntimeError): switch_owned(p, UID, OLD, NEW)
        p.stop.assert_not_called()
        p.play_uri.assert_not_called()
    def test_never_play_when_after_stop_group_changes(self):
        p = fake()
        def stop():
            p.get_current_transport_info.return_value = {'current_transport_state':'STOPPED'}
            p.group.members.append(Mock(uid='OTHER'))
        with self.assertRaises(RuntimeError): switch_owned(p, UID, OLD, NEW, stop=stop)
        p.play_uri.assert_not_called()

if __name__=='__main__': unittest.main()
