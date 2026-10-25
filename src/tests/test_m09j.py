import pathlib
import queue
import sys
import threading
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sonos_parallel_test import check_target, wait_reader

TARGET_UID = 'TEST_TARGET_UID'

class DummyPlayer:
    def __init__(self, uid=TARGET_UID, state='STOPPED', other=False):
        self.uid = uid
        self.player_name = 'Test Speaker'
        self.other = other
        self._state = state
        members = [self]
        if other:
            members.append(SimpleNamespace(uid='TEST_OTHER_UID', player_name='Other Test Speaker'))
        self.group = SimpleNamespace(members=members, coordinator=self)
    def get_current_transport_info(self):
        return {'current_transport_state': self._state}

class TestSafety(unittest.TestCase):
    def test_solo_stopped(self):
        self.assertEqual(check_target(DummyPlayer(), TARGET_UID), 'STOPPED')
    def test_group_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Einzelgruppe'):
            check_target(DummyPlayer(other=True), TARGET_UID)
    def test_playing_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'STOPPED'):
            check_target(DummyPlayer(state='PLAYING'), TARGET_UID)
    def test_uid_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'UID'):
            check_target(DummyPlayer(uid='wrong'), TARGET_UID)
    def test_wait_reader(self):
        q = queue.Queue()
        proc = SimpleNamespace(poll=lambda: None)
        q.put((0, 'Serving http://0.0.0.0:8765/track/1.wav'))
        q.put((0, '[+0.01s] READER started'))
        wait_reader(q, proc, .1)
    def test_reader_finished_refused(self):
        q = queue.Queue()
        proc = SimpleNamespace(poll=lambda: None)
        q.put((0, 'Serving http://0.0.0.0:8765/track/1.wav'))
        q.put((0, '[+0.01s] READER end written=0 error=X'))
        with self.assertRaisesRegex(RuntimeError, 'bereits fertig'):
            wait_reader(q, proc, .1)

if __name__ == '__main__': unittest.main()
