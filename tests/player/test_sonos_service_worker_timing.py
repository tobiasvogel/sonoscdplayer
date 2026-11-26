"""Timing is applied to actual worker operations, not cached status RPCs."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from sonoscdplayer.player.remote import request
from sonoscdplayer.sonos.network_timing import TimingDiagnostics
from test_player_service import DummyController, start_test_service


class ServiceTimingTests(unittest.TestCase):
    def test_worker_commands_and_refresh_are_timed(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = str(Path(temp.name) / 'control.sock')
        controller = DummyController()
        controller.network_timing = TimingDiagnostics(True, threshold=0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            start_test_service(self, controller, path, poll_seconds=0.05)
            self.assertTrue(request(path, 'next')['ok'])
            self.assertFalse(request(path, 'stop')['ok'])
            self.assertTrue(request(path, 'status')['ok'])
        self.assertEqual(controller.calls, ['next', 'stop'])
        text = output.getvalue()
        self.assertIn('operation=refresh', text)
        self.assertIn('operation=command command=next', text)
        self.assertIn('operation=command command=stop', text)
        self.assertIn('outcome=rejected', text)
        self.assertNotIn('operation=command command=status', text)
