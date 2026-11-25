"""AUD-05b: transient Sonos reads have bounded grace without unsafe STOP."""
from unittest.mock import patch

from sonoscdplayer.player.controller import Controller, SONOS_READ_GRACE_SECONDS
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


def test_single_transport_timeout_preserves_stream_then_recovers():
    controller, speaker, server = _playing_controller()
    owned_url = controller.owned_url
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=TimeoutError('temporary Sonos timeout')):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert server.alive() and controller.owned_url == owned_url
    assert speaker.stops == 0
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller._sonos_read_failure_since is None
    assert server.alive() and speaker.stops == 0


def test_track_info_timeout_preserves_stream():
    controller, speaker, server = _playing_controller()
    with patch.object(speaker, 'get_current_track_info',
                      side_effect=TimeoutError('temporary Sonos track info timeout')):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert server.alive()
    assert speaker.stops == 0


def test_sustained_timeout_expires_without_sonos_stop():
    controller, speaker, server = _playing_controller()
    with (patch.object(speaker, 'get_current_transport_info',
                       side_effect=TimeoutError('offline')),
          patch('sonoscdplayer.player.controller.time.monotonic') as clock):
        clock.return_value = 100.0
        controller.refresh()
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS - 0.1
        controller.refresh()
        assert controller.state.mode == Mode.PLAYING and server.alive()
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS
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


def test_foreign_takeover_during_grace_still_fails_closed():
    controller, speaker, server = _playing_controller()
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=TimeoutError('offline')):
        controller.refresh()
    assert server.alive()
    speaker.uri = 'http://external.example/replaced'
    controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert not server.alive()
    assert speaker.stops == 0


def test_nontransient_programming_error_still_fails_closed():
    controller, speaker, server = _playing_controller()
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=ValueError('invalid Sonos data')):
        controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert not server.alive()
    assert speaker.stops == 0
