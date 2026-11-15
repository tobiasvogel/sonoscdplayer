"""M0.15a-r2: nonblocking status must remain readable during long PLAY."""
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from player_service import StatusPublisher, serve


class FakeServer:
    def alive(self): return False
    def reader_active(self): return False


class FakeState:
    def __init__(self):
        self.mode = type('M', (), {'value': 'STOPPED'})()
        self.error = None
        self.selected_track = 1
        self.track_numbers = [1]


class BlockingController:
    def __init__(self):
        self.state = FakeState()
        self.server = FakeServer()
        self.uid = None
        self.owned_url = None
        self.last_transport = 'STOPPED'
        self.status_hook = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def status(self):
        return f'mode={self.state.mode.value} track=1/1 sonos={self.last_transport} owned=False server=False'

    def status_snapshot(self):
        return {'schema_version': 1, 'player': {'mode': self.state.mode.value, 'track': 1, 'track_count': 1, 'error': None},
                'sonos': {'target_uid': None, 'transport': self.last_transport, 'owned': False},
                'stream': {'active': False}}

    def refresh(self):
        self.calls += 1

    def play(self, uid):
        self.state.mode = type('M', (), {'value': 'STARTING'})()
        self.status_hook()
        self.entered.set()
        self.release.wait(4)
        self.state.mode = type('M', (), {'value': 'PLAYING'})()


def request(path, payload, timeout=1.0):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall(json.dumps(payload).encode() + b'\n')
        data = b''
        while not data.endswith(b'\n'):
            data += sock.recv(4096)
        return json.loads(data)


class ResponsiveStatusTest(unittest.TestCase):
    def test_status_is_served_during_blocking_play(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / 'control.sock')
            controller = BlockingController()
            errors = []
            def worker():
                try: serve(controller, path, poll_seconds=0.03)
                except BaseException as ex: errors.append(ex)
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            for _ in range(200):
                if Path(path).exists(): break
                time.sleep(0.01)
            self.assertTrue(Path(path).exists())
            play_result = []
            play_thread = threading.Thread(target=lambda: play_result.append(request(path, {'command':'play','uid':'TEST_UID'},timeout=5)), daemon=True)
            play_thread.start()
            try:
                self.assertTrue(controller.entered.wait(2))
                start = time.monotonic()
                status = request(path, {'command':'status'})
                elapsed = time.monotonic() - start
                self.assertLess(elapsed, 0.7)
                self.assertEqual(status['state']['player']['mode'], 'STARTING')
                self.assertFalse(status['state']['sonos']['owned'])
                self.assertTrue(play_thread.is_alive())
            finally:
                controller.release.set()
            play_thread.join(5)
            self.assertTrue(play_result[0]['ok'])

    def test_snapshot_copy_cannot_mutate_published_state(self):
        c=BlockingController()
        p=StatusPublisher(c)
        result=p.read();result['state']['player']['mode']='WRONG'
        self.assertEqual(p.read()['state']['player']['mode'],'STOPPED')

if __name__ == '__main__': unittest.main()
