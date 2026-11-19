"""Continue with the next track after a track has played out; track length
for Sonos."""
import unittest
from unittest.mock import patch

from sonoscdplayer.player.state import Mode
from sonoscdplayer.sonos.track_metadata import track_didl

import test_player_sonos_queue as queue_tests
from test_player_sonos_queue import META, QueuePlayer


class ContinueTests(unittest.TestCase):
    """Without the queue block (here: use_queue off) the player continues itself."""
    setUp = queue_tests.QueueTests.setUp
    playing = queue_tests.QueueTests.playing

    def make(self, player, **kwargs):
        kwargs.setdefault('use_queue', False)
        return queue_tests.QueueTests.make(self, player, **kwargs)

    def end_track(self, elapsed, duration=200):
        self.c._display_progress = {'elapsed_seconds': elapsed, 'duration_seconds': duration}
        self.p.transport = 'STOPPED'
        self.c.refresh()

    def test_next_track_follows_a_played_out_track(self):
        self.make(QueuePlayer())
        self.playing()
        self.end_track(197)
        self.assertEqual((self.c.state.mode, self.c.state.selected_track), (Mode.STARTING, 2))
        self.assertEqual(self.p.plays[-1], 'http://192.0.2.10:8765/track/2.wav')

    def test_stop_from_the_sonos_app_does_not_continue(self):
        self.make(QueuePlayer())
        self.playing()
        self.end_track(60)
        self.assertEqual((self.c.state.mode, self.c.state.selected_track), (Mode.STOPPED, 1))
        self.assertEqual(len(self.p.plays), 1)

    def test_block_mode_leaves_continuing_to_sonos(self):
        self.make(QueuePlayer(), use_queue=True)
        self.playing()
        self.end_track(197)          # still our URL: Sonos stopped at the end itself
        self.assertEqual(self.c.state.mode, Mode.STOPPED)
        self.assertEqual(len(self.p.plays), 1)

    def test_without_progress_no_continue(self):
        self.make(QueuePlayer())
        self.playing()
        self.c._display_progress = None
        self.p.transport = 'STOPPED'
        self.c.refresh()
        self.assertEqual(self.c.state.mode, Mode.STOPPED)

    def test_last_track_stops(self):
        self.make(QueuePlayer())
        self.c.state = self.c.state.select(3)
        self.playing()
        self.end_track(199)
        self.assertEqual(self.c.state.mode, Mode.STOPPED)

    def test_can_be_switched_off(self):
        self.make(QueuePlayer())
        self.c.auto_advance = False
        self.playing()
        self.end_track(199)
        self.assertEqual(self.c.state.mode, Mode.STOPPED)

    def test_failed_continue_is_an_error_not_a_crash(self):
        self.make(QueuePlayer())
        self.playing()
        with patch.object(self.s, 'start', side_effect=RuntimeError('no PCM')):
            self.end_track(199)
        self.assertEqual(self.c.state.mode, Mode.ERROR)


class DurationTests(unittest.TestCase):
    def test_duration_from_metadata_or_toc(self):
        didl = track_didl('http://h/track/1.wav', META, 1, duration_seconds=205.4)
        self.assertIn('duration="0:03:25"', didl)
        meta = {'tracks': [{'number': 1, 'title': 'x', 'duration_seconds': 3725.0}]}
        self.assertIn('duration="1:02:05"', track_didl('http://h/track/1.wav', meta, 1))
        self.assertNotIn('duration=', track_didl('http://h/track/1.wav', {'tracks': []}, 1))


if __name__ == '__main__':
    unittest.main()
