import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest

from player_service import handle_command, serve
from player_remote import request


class DummyController:
    def __init__(self):
        self.calls = []
        self.refreshes = 0
    def status(self):
        return 'mode=STOPPED track=2/15 sonos=UNKNOWN owned=False server=False'
    def play(self, uid):
        self.calls.append(('play', uid))
    def command(self, cmd):
        self.calls.append(cmd)
        if cmd == 'stop':
            raise ValueError('not owner')
    def refresh(self):
        self.refreshes += 1


class ServiceTests(unittest.TestCase):
    def test_commands_and_rejects(self):
        c = DummyController()
        for name in ('next','previous','select 3'):
            self.assertTrue(handle_command(c, {'command': name})['ok'])
        self.assertTrue(handle_command(c, {'command':'play','uid':'TEST_UID'})['ok'])
        self.assertEqual(c.calls, ['next','previous','select 3',('play','TEST_UID')])
        self.assertFalse(handle_command(c, {'command':'play'})['ok'])
        self.assertFalse(handle_command(c, {'command':'status','uid':'TEST_UID'})['ok'])
        for payload in ({'command': 'quit'}, {'command': 'bad'}, {'command':'status','extra':1},
                        {'command': 22}, [], {'command': 'select 1234'}):
            self.assertFalse(handle_command(c, payload)['ok'])
        self.assertFalse(handle_command(c, {'command': 'stop'})['ok'])
        self.assertEqual(c.calls[-1], 'stop')
        self.assertTrue(handle_command(c, {'command': 'status'})['ok'])
        self.assertTrue(handle_command(c, {'command': 'help'})['ok'])

    def test_live_local_socket_permissions_and_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / 'control.sock')
            c = DummyController()
            thread = threading.Thread(target=serve, args=(c, path), kwargs={'poll_seconds': 0.01}, daemon=True)
            thread.start()
            for _ in range(100):
                if os.path.exists(path):
                    break
                time.sleep(0.01)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertTrue(request(path, 'status')['ok'])
            self.assertTrue(request(path, 'next')['ok'])
            self.assertEqual(c.calls, ['next'])
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(path)
                s.sendall(b'X'*300 + b'\n')
                self.assertFalse(json.loads(s.recv(4096))['ok'])
            self.assertGreater(c.refreshes, 0)

    def test_slow_play_receives_reply_beyond_old_five_second_limit(self):
        class SlowController(DummyController):
            def play(self, uid):
                time.sleep(0.20)
                super().play(uid)
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / 'control.sock')
            c = SlowController()
            thread = threading.Thread(target=serve, args=(c, path),
                                      kwargs={'poll_seconds': 0.01}, daemon=True)
            thread.start()
            for _ in range(100):
                if os.path.exists(path):
                    break
                time.sleep(0.01)
            self.assertTrue(request(path, 'play', uid='TEST_UID', response_timeout=1)['ok'])
            self.assertEqual(c.calls, [('play','TEST_UID')])

    def test_response_timeout_is_not_a_command_failure(self):
        from unittest.mock import patch
        import io
        from contextlib import redirect_stderr
        from player_remote import run
        err = io.StringIO()
        with patch('player_remote.request', side_effect=socket.timeout), redirect_stderr(err):
            self.assertEqual(run(['--uid','TEST_UID','play']), 2)
        self.assertIn('outcome UNKNOWN', err.getvalue())
        self.assertIn('Do NOT blindly repeat', err.getvalue())

    def test_existing_path_not_unlinked(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'control.sock'
            p.write_text('keep')
            with self.assertRaises(FileExistsError):
                serve(DummyController(), str(p))
            self.assertEqual(p.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
