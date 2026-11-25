"""Sonos HTTP read failures must not interrupt a healthy local stream."""
from unittest.mock import patch

import pytest
from requests.exceptions import ConnectionError, Timeout

from sonoscdplayer.player.controller import SONOS_READ_GRACE_SECONDS
from sonoscdplayer.player.state import Mode
from test_aud05a_sonos_transient_read_characterization import _playing_controller


@pytest.mark.parametrize('error_type', [Timeout, ConnectionError])
@pytest.mark.parametrize('method', ['get_current_transport_info', 'get_current_track_info'])
def test_http_read_failure_preserves_stream_then_recovers(error_type, method):
    controller, speaker, server = _playing_controller()
    owned_url = controller.owned_url
    with patch.object(speaker, method, side_effect=error_type('temporary HTTP failure')):
        controller.refresh()

    assert controller.state.mode == Mode.PLAYING
    assert server.alive()
    assert controller.owned_url == owned_url
    assert speaker.stops == 0
    assert speaker.plays == [owned_url]
    assert server.starts == [1]

    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller._sonos_read_failure_since is None
    assert server.alive()
    assert speaker.stops == 0
    assert speaker.plays == [owned_url]
    assert server.starts == [1]


@pytest.mark.parametrize('error_type', [Timeout, ConnectionError])
def test_sustained_http_read_failure_expires_without_sonos_stop(error_type):
    controller, speaker, server = _playing_controller()
    owned_url = controller.owned_url
    with (patch.object(speaker, 'get_current_transport_info',
                       side_effect=error_type('Sonos offline')),
          patch('sonoscdplayer.player.controller.time.monotonic') as clock):
        clock.return_value = 100.0
        controller.refresh()
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS - 0.1
        controller.refresh()
        assert controller.state.mode == Mode.PLAYING
        assert server.alive()
        assert controller.owned_url == owned_url

        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS
        controller.refresh()

    assert controller.state.mode == Mode.ERROR
    assert not server.alive()
    assert controller.owned_url is None
    assert speaker.stops == 0
    assert speaker.plays == [owned_url]
    assert server.starts == [1]
