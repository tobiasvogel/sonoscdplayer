"""Service replies must preserve the controller's network safety decisions."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.controller import SONOS_READ_GRACE_SECONDS
from sonoscdplayer.player.service import StatusPublisher, handle_command
from sonoscdplayer.player.state import Mode
from test_aud05a_sonos_transient_read_characterization import _playing_controller
from test_sonos_queue_transition_reads import _queued_controller


def assert_snapshot(reply, mode, *, track=1, active=True):
    state = reply['state']
    assert state['player']['mode'] == mode.value
    assert state['player']['track'] == track
    assert state['sonos']['owned'] is active
    assert state['stream']['active'] is active


@pytest.mark.parametrize('command', ['pause', 'resume'])
def test_service_read_timeout_is_reported_without_releasing_session(command):
    controller, speaker, server = _playing_controller()
    mode = Mode.PLAYING if command == 'pause' else Mode.PAUSED
    if command == 'resume':
        speaker.transport = 'PAUSED_PLAYBACK'
        controller.refresh()
    transport = 'PAUSED_PLAYBACK' if command == 'pause' else 'PLAYING'
    write = Mock(side_effect=lambda: setattr(speaker, 'transport', transport))
    setattr(speaker, command if command == 'pause' else 'play', write)
    url = controller.owned_url
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=ReadTimeout('offline')):
        controller.refresh()
        since = controller._sonos_read_failure_since
        assert since is not None
        reply = handle_command(controller, {'command': command})
        assert reply['ok'] is False
        assert 'ReadTimeout' in reply['error']
        assert_snapshot(reply, mode)
        assert controller._sonos_read_failure_since == since
        write.assert_not_called()
        assert controller.owned_url == url
        assert server.alive() and speaker.stops == 0
    controller.refresh()
    assert controller._sonos_read_failure_since is None
    reply = handle_command(controller, {'command': command})
    assert reply['ok'] is True
    assert_snapshot(reply, Mode.PAUSED if command == 'pause' else Mode.PLAYING)
    write.assert_called_once_with()
    assert server.starts == [1] and speaker.stops == 0


@pytest.mark.parametrize('applied', [False, True])
def test_service_ambiguous_resume_is_reported_and_observed_not_retried(applied):
    controller, speaker, server = _playing_controller()
    speaker.transport = 'PAUSED_PLAYBACK'
    controller.refresh()
    url = controller.owned_url

    def uncertain_play():
        if applied:
            speaker.transport = 'PLAYING'
        raise ReadTimeout('PLAY outcome unknown')

    speaker.play = Mock(side_effect=uncertain_play)
    reply = handle_command(controller, {'command': 'resume'})
    assert reply['ok'] is False and 'ReadTimeout' in reply['error']
    assert_snapshot(reply, Mode.PAUSED)
    controller.refresh()
    reply = handle_command(controller, {'command': 'status'})
    assert reply['ok'] is True
    assert_snapshot(reply, Mode.PLAYING if applied else Mode.PAUSED)
    speaker.play.assert_called_once_with()
    assert controller.owned_url == url and server.alive()
    assert server.starts == [1] and speaker.stops == 0


def test_service_refused_queue_destination_preserves_current_playback():
    controller, speaker, server = _queued_controller()
    url = controller.owned_url
    speaker.queue[1] = 'http://foreign.example/track'
    speaker.get_queue = Mock(side_effect=lambda start, max_items: [
        SimpleNamespace(resources=[SimpleNamespace(uri=item)])
        for item in speaker.queue[start:start + max_items]
    ])
    speaker.play_from_queue = Mock(wraps=speaker.play_from_queue)
    reply = handle_command(controller, {'command': 'next'})
    assert reply['ok'] is False and 'RuntimeError' in reply['error']
    assert_snapshot(reply, Mode.PLAYING)
    speaker.play_from_queue.assert_not_called()
    controller.refresh()
    assert controller.owned_url == url and server.alive()
    assert speaker.stops == 0 and server.starts == [1]


@pytest.mark.parametrize('request_data', [None, {'command': []}, {'command': 'unknown'}])
def test_invalid_service_request_does_not_prevent_following_valid_command(request_data):
    controller, speaker, server = _playing_controller()
    url = controller.owned_url
    reply = handle_command(controller, request_data)
    assert reply['ok'] is False
    assert_snapshot(reply, Mode.PLAYING)
    assert controller.owned_url == url and server.alive()
    assert speaker.stops == 0
    reply = handle_command(controller, {'command': 'stop'})
    assert reply['ok'] is True
    assert_snapshot(reply, Mode.STOPPED, active=False)
    assert speaker.stops == 1 and not server.alive()


def test_cached_status_during_grace_and_after_expiry():
    controller, speaker, server = _playing_controller()
    publisher = StatusPublisher(controller)
    with (patch.object(speaker, 'get_current_transport_info',
                       side_effect=ReadTimeout('offline')) as transport,
          patch.object(speaker, 'get_current_track_info',
                       side_effect=ReadTimeout('offline')) as track,
          patch('sonoscdplayer.player.controller.time.monotonic') as clock):
        clock.return_value = 100.0
        controller.refresh()
        assert controller._sonos_read_failure_since == 100.0
        publisher.publish(controller)
        reads = (transport.call_count, track.call_count)
        reply = publisher.read()
        assert reply['ok'] is True
        assert_snapshot(reply, Mode.PLAYING)
        # A caller cannot mutate the cached reply or trigger Sonos reads.
        reply['state']['player']['mode'] = 'changed'
        assert_snapshot(publisher.read(), Mode.PLAYING)
        assert_snapshot(handle_command(controller, {'command': 'status'}), Mode.PLAYING)
        assert (transport.call_count, track.call_count) == reads
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS
        controller.refresh()
        publisher.publish(controller)
        assert_snapshot(publisher.read(), Mode.ERROR, active=False)
    assert not server.alive() and speaker.stops == 0
