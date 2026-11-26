"""RESUME requires fresh ownership and never retries ambiguous writes."""
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.state import Mode
from test_aud05a_sonos_transient_read_characterization import _playing_controller
from test_sonos_queue_transition_reads import _queued_controller


@pytest.fixture(params=[False, True], ids=['direct', 'queue'])
def paused(request):
    controller, speaker, server = (_queued_controller() if request.param
                                   else _playing_controller())
    speaker.transport = 'PAUSED_PLAYBACK'
    controller.refresh()
    assert controller.state.mode == Mode.PAUSED
    speaker.play = Mock(side_effect=lambda: setattr(speaker, 'transport', 'PLAYING'))
    return controller, speaker, server


def assert_session(controller, speaker, server, url, mode):
    assert controller.state.mode == mode
    assert controller.owned_url == url
    assert controller.player is speaker
    assert server.alive()
    assert server.starts == [1]
    assert speaker.stops == 0


def test_resume_requires_fresh_reads_during_grace(paused):
    controller, speaker, server = paused
    url = controller.owned_url
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=ReadTimeout('offline')):
        controller.refresh()
        since = controller._sonos_read_failure_since
        assert since is not None
        with pytest.raises(ReadTimeout):
            controller.resume()
        assert controller._sonos_read_failure_since == since
        speaker.play.assert_not_called()
        assert_session(controller, speaker, server, url, Mode.PAUSED)
    controller.refresh()
    assert controller._sonos_read_failure_since is None
    controller.resume()
    speaker.play.assert_called_once_with()
    assert_session(controller, speaker, server, url, Mode.PLAYING)


def test_resume_second_ownership_check_must_succeed(paused):
    controller, speaker, server = paused
    url = controller.owned_url
    original = controller._ownership
    checks = Mock(side_effect=[None, ReadTimeout('second check unavailable')])

    def ownership(player, expected):
        checks(player, expected)
        return original(player, expected)

    with patch.object(controller, '_ownership', side_effect=ownership):
        with pytest.raises(ReadTimeout):
            controller.resume()
    assert checks.call_count == 2
    speaker.play.assert_not_called()
    assert_session(controller, speaker, server, url, Mode.PAUSED)


@pytest.mark.parametrize('applied', [False, True])
def test_resume_ambiguous_write_is_observed_not_retried(paused, applied):
    controller, speaker, server = paused
    url = controller.owned_url

    def uncertain_play():
        if applied:
            speaker.transport = 'PLAYING'
        raise ReadTimeout('PLAY outcome unknown')

    speaker.play.side_effect = uncertain_play
    with pytest.raises(ReadTimeout):
        controller.resume()
    assert_session(controller, speaker, server, url, Mode.PAUSED)
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=ReadTimeout('offline')):
        controller.refresh()
        assert controller._sonos_read_failure_since is not None
        assert_session(controller, speaker, server, url, Mode.PAUSED)
    controller.refresh()
    assert controller._sonos_read_failure_since is None
    assert_session(controller, speaker, server, url,
                   Mode.PLAYING if applied else Mode.PAUSED)
    speaker.play.assert_called_once_with()


def test_resume_foreign_takeover_during_grace_is_refused(paused):
    controller, speaker, server = paused
    url = controller.owned_url
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=ReadTimeout('offline')):
        controller.refresh()
    assert controller._sonos_read_failure_since is not None
    speaker.uri = 'http://foreign.example/stream'
    with pytest.raises(RuntimeError):
        controller.resume()
    speaker.play.assert_not_called()
    assert_session(controller, speaker, server, url, Mode.PAUSED)
    with patch('sonoscdplayer.player.controller.remove_queue_block', return_value=0):
        controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert controller.owned_url is None
    assert not server.alive()
    assert speaker.stops == 0
    speaker.play.assert_not_called()
