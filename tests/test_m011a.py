import unittest
from player_state import PlayerState, Mode, TransitionError


class PlayerStateTests(unittest.TestCase):
    def setUp(self):
        self.ready = PlayerState().disc_loaded((1, 2, 3))

    def test_load_disc_and_track_selection(self):
        self.assertEqual(self.ready.mode, Mode.STOPPED)
        self.assertEqual(self.ready.selected_track, 1)
        self.assertEqual(self.ready.select(3).selected_track, 3)
        self.assertEqual(self.ready.selected_track, 1)

    def test_next_previous_boundaries(self):
        self.assertEqual(self.ready.previous_track().selected_track, 1)
        self.assertEqual(self.ready.select(3).next_track().selected_track, 3)
        self.assertEqual(self.ready.next_track().previous_track().selected_track, 1)

    def test_reject_invalid_track(self):
        with self.assertRaises(TransitionError): self.ready.select(0)
        with self.assertRaises(TransitionError): self.ready.select(4)

    def test_lifecycle(self):
        starting = self.ready.start_requested()
        self.assertEqual(starting.mode, Mode.STARTING)
        playing = starting.playback_confirmed()
        self.assertEqual(playing.mode, Mode.PLAYING)
        self.assertEqual(playing.natural_end().mode, Mode.STOPPED)

    def test_no_mutation_while_playing(self):
        playing = self.ready.start_requested().playback_confirmed()
        for action in (lambda: playing.next_track(), lambda: playing.select(2),
                       lambda: playing.disc_removed(), lambda: playing.start_requested()):
            with self.assertRaises(TransitionError): action()

    def test_no_disc(self):
        with self.assertRaises(TransitionError): PlayerState().start_requested()
        self.assertEqual(self.ready.disc_removed().mode, Mode.NO_DISC)

    def test_error_reset(self):
        broken = self.ready.failed('drive missing')
        self.assertEqual(broken.mode, Mode.ERROR)
        self.assertEqual(broken.reset_error().mode, Mode.STOPPED)
        self.assertEqual(PlayerState().failed('no disc').reset_error().mode, Mode.NO_DISC)

    def test_invalid_transitions(self):
        with self.assertRaises(TransitionError): self.ready.playback_confirmed()
        with self.assertRaises(TransitionError): self.ready.natural_end()
        with self.assertRaises(TransitionError): self.ready.reset_error()
        with self.assertRaises(ValueError): PlayerState(track_numbers=(2,1))
