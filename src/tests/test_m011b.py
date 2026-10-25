import pathlib
import sys
import unittest
from unittest.mock import Mock
from types import SimpleNamespace
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from player_play import make_url, prepare_selection, confirmed_mode
from player_state import Mode, PlayerState, TransitionError

class PlayTests(unittest.TestCase):
    def setUp(self):
        self.disc = SimpleNamespace(tracks=[SimpleNamespace(number=i) for i in range(1, 4)],
                                    get_track=lambda n: {1: 'one', 2: 'two', 3: 'three'}[n])

    def test_selected_track_two(self):
        state = prepare_selection(self.disc, 2)
        self.assertEqual((state.mode, state.selected_track), (Mode.STOPPED, 2))

    def test_out_of_range_rejected(self):
        with self.assertRaises(TransitionError): prepare_selection(self.disc, 4)

    def test_state_confirmed_only_by_playing(self):
        state = prepare_selection(self.disc, 2).start_requested()
        self.assertEqual(confirmed_mode(state, 'TRANSITIONING').mode, Mode.STARTING)
        self.assertEqual(confirmed_mode(state, 'STOPPED').mode, Mode.STARTING)
        self.assertEqual(confirmed_mode(state, 'PLAYING').mode, Mode.PLAYING)

    def test_natural_end_after_confirmed_playback(self):
        state = prepare_selection(self.disc, 2).start_requested().playback_confirmed()
        self.assertEqual(confirmed_mode(state, 'STOPPED').mode, Mode.STOPPED)

    def test_url_selects_track_without_private_defaults(self):
        self.assertEqual(make_url('192.0.2.12', 8765, 2), 'http://192.0.2.12:8765/track/2.wav')

    def test_url_rejects_invalid_values(self):
        for host in ['localhost', '127.0.0.1', '0.0.0.0', '192.0.2.3/x', 'bad']:
            with self.subTest(host=host), self.assertRaises(ValueError): make_url(host, 8765, 2)
        with self.assertRaises(ValueError): make_url('192.0.2.12', 0, 2)
        with self.assertRaises(ValueError): make_url('192.0.2.12', 8765, 0)

if __name__ == '__main__': unittest.main()
