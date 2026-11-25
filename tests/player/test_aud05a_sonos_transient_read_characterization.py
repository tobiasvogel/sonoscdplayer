"""AUD-05a: characterize isolated Sonos read failures before recovery changes.

These tests document the current fail-closed behavior; they intentionally do
not claim that transient timeouts are safely recoverable yet.
"""
from unittest.mock import patch

from sonoscdplayer.player.controller import Controller
from sonoscdplayer.player.state import Mode, PlayerState

from test_player_controller import FakePlayer, FakeServer


def _playing_controller():
    speaker = FakePlayer()
    server = FakeServer()
    controller = Controller(
        PlayerState().disc_loaded((1, 2, 3)),
        speaker.uid, '192.0.2.10', 8765, server,
        verify=lambda uid: speaker,
    )
    controller.use_queue = False
    controller.play()
    speaker.transport = 'PLAYING'
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    return controller, speaker, server


def test_single_transport_timeout_abandons_local_stream_without_sonos_stop():
    """One transient read failure currently closes the locally owned stream."""
    controller, speaker, server = _playing_controller()
    owned_url = controller.owned_url
    with patch.object(
        speaker, 'get_current_transport_info',
        side_effect=TimeoutError('temporary Sonos request timeout'),
    ):
        controller.refresh()

    assert controller.state.mode == Mode.ERROR
    assert not server.alive()
    assert controller.owned_url is None
    assert speaker.stops == 0
    # The fake speaker still reports the old stream; local audio vanished.
    assert speaker.uri == owned_url
    assert speaker.transport == 'PLAYING'

    controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert not server.alive()
    assert speaker.stops == 0


def test_single_track_info_timeout_has_same_terminal_effect():
    controller, speaker, server = _playing_controller()
    with patch.object(
        speaker, 'get_current_track_info',
        side_effect=TimeoutError('temporary Sonos track info timeout'),
    ):
        controller.refresh()

    assert controller.state.mode == Mode.ERROR
    assert not server.alive()
    assert speaker.stops == 0


def test_foreign_uri_still_fails_closed_without_stopping_sonos():
    controller, speaker, server = _playing_controller()
    speaker.uri = 'http://external.example/not-our-stream'
    controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert not server.alive()
    assert speaker.stops == 0
