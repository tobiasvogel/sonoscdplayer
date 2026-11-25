"""Sonos read grace belongs to an individual playback session."""
from unittest.mock import patch

import pytest

from sonoscdplayer.player.controller import SONOS_READ_GRACE_SECONDS
from sonoscdplayer.player.state import Mode
from test_aud05a_sonos_transient_read_characterization import _playing_controller


def _timeout(controller, speaker):
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=TimeoutError('offline')):
        controller.refresh()


def test_repeated_outages_get_independent_grace_after_success():
    controller, speaker, server = _playing_controller()
    with patch('sonoscdplayer.player.controller.time.monotonic') as clock:
        clock.return_value = 100.0
        _timeout(controller, speaker)
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS - 0.1
        controller.refresh()
        assert controller._sonos_read_failure_since is None

        clock.return_value = 200.0
        _timeout(controller, speaker)
        clock.return_value = 200.0 + SONOS_READ_GRACE_SECONDS - 0.1
        _timeout(controller, speaker)
        assert controller.state.mode == Mode.PLAYING
        assert server.alive()
        clock.return_value = 200.0 + SONOS_READ_GRACE_SECONDS
        _timeout(controller, speaker)

    assert controller.state.mode == Mode.ERROR
    assert not server.alive()
    assert speaker.stops == 0
    assert server.starts == [1]


@pytest.mark.parametrize('end_session', ['stop', 'natural_end', 'error_reset'])
def test_new_playback_gets_fresh_grace_before_first_confirmation(end_session):
    controller, speaker, server = _playing_controller()
    with patch('sonoscdplayer.player.controller.time.monotonic') as clock:
        clock.return_value = 100.0
        _timeout(controller, speaker)
        assert controller.state.mode == Mode.PLAYING
        assert server.alive()

        if end_session == 'stop':
            controller.stop()
        elif end_session == 'natural_end':
            speaker.transport = 'STOPPED'
            controller.refresh()
        else:
            clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS
            _timeout(controller, speaker)
            assert controller.state.mode == Mode.ERROR
            assert not server.alive()
            speaker.transport = 'STOPPED'
            controller.reset()
        assert controller.state.mode == Mode.STOPPED
        assert not server.alive()
        expected_stops = int(end_session == 'stop')
        assert speaker.stops == expected_stops

        clock.return_value = 200.0
        controller.play(speaker.uid)
        assert controller.state.mode == Mode.STARTING
        owned_url = controller.owned_url
        _timeout(controller, speaker)
        assert controller.state.mode == Mode.STARTING
        assert server.alive()
        assert controller.owned_url == owned_url
        assert controller._sonos_read_failure_since == 200.0

        clock.return_value = 200.0 + SONOS_READ_GRACE_SECONDS - 0.1
        _timeout(controller, speaker)
        assert controller.state.mode == Mode.STARTING
        assert server.alive()
        speaker.transport = 'PLAYING'
        controller.refresh()

    assert controller.state.mode == Mode.PLAYING
    assert controller._sonos_read_failure_since is None
    assert server.alive()
    assert speaker.stops == expected_stops
    assert server.starts == [1, 1]
    assert len(speaker.plays) == 2


def test_stop_during_outage_requires_live_ownership():
    controller, speaker, server = _playing_controller()
    owned_url = controller.owned_url
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=TimeoutError('offline')):
        controller.refresh()
        with pytest.raises(TimeoutError):
            controller.stop()
    assert controller.state.mode == Mode.PLAYING
    assert server.alive()
    assert controller.owned_url == owned_url
    assert speaker.stops == 0
    controller.stop()
    assert controller.state.mode == Mode.STOPPED
    assert not server.alive()
    assert speaker.stops == 1
