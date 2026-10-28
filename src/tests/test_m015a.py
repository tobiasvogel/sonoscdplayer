import queue
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from player_controller import wait_startup_pcm, ProcessServer

class Tests(unittest.TestCase):
    def test_ready(self):
        q=queue.Queue(); q.put((0,'[+ 1.0s] STARTUP_PCM_READY bytes=58800'))
        wait_startup_pcm(q, SimpleNamespace(poll=lambda:None), timeout=.05)
    def test_reader_end_fails(self):
        q=queue.Queue(); q.put((0,'READER end written=0/100 error=IOError'))
        with self.assertRaisesRegex(RuntimeError, 'ended'):
            wait_startup_pcm(q, SimpleNamespace(poll=lambda:None), timeout=.05)
    def test_exit_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'exited'):
            wait_startup_pcm(queue.Queue(), SimpleNamespace(poll=lambda:1), timeout=.05)
    def test_timeout_fails_closed(self):
        with self.assertRaisesRegex(TimeoutError, 'Sonos PLAY not sent'):
            wait_startup_pcm(queue.Queue(), SimpleNamespace(poll=lambda:None), timeout=.02)
    def test_server_default_enabled(self):
        p=ProcessServer('.', '/dev/sr0', 'config.toml', 8765)
        self.assertTrue(p.startup_prebuffer)
        self.assertEqual(p.startup_timeout,45)
