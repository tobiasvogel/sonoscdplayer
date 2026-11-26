"""Queue jumps require fresh ownership of both current and destination tracks."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.state import Mode
from test_sonos_queue_transition_reads import _queued_controller


FOREIGN = 'http://foreign.example/track'


@pytest.fixture(params=['next', 'previous'])
def jump(request):
    controller, speaker, server = _queued_controller()
    speaker.uri = speaker.queue[1]
    controller.refresh()
    assert controller.state.selected_track == 2
    assert controller.state.mode == Mode.PLAYING
    speaker.get_queue = Mock(side_effect=lambda start, max_items: [
        SimpleNamespace(resources=[SimpleNamespace(uri=url)])
        for url in speaker.queue[start:start + max_items]
    ])
    speaker.play_from_queue = Mock(wraps=speaker.play_from_queue)
    target = 2 if request.param == 'next' else 0
    return controller, speaker, server, request.param, target


def assert_session(controller, speaker, server, track, url):
    assert controller.state.mode == Mode.PLAYING
    assert controller.state.selected_track == track
    assert controller.owned_url == url
    assert controller.player is speaker
    assert server.alive() and server.starts == [1]
    assert speaker.stops == 0 and speaker.plays == []


def test_queue_jump_with_intact_destination(jump):
    controller, speaker, server, direction, target = jump
    url = speaker.queue[target]
    controller.change(direction)
    speaker.play_from_queue.assert_called_once_with(target)
    assert_session(controller, speaker, server, target + 1, url)


def test_queue_jump_refuses_unreachable_current_ownership(jump):
    controller, speaker, server, direction, target = jump
    url = controller.owned_url
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=ReadTimeout('transport unavailable')):
        with pytest.raises(ReadTimeout):
            controller.change(direction)
    speaker.play_from_queue.assert_not_called()
    assert_session(controller, speaker, server, 2, url)


def test_queue_jump_requires_readable_destination_then_recovers(jump):
    controller, speaker, server, direction, target = jump
    url = controller.owned_url
    with patch.object(speaker, 'get_queue', side_effect=ReadTimeout('queue unavailable')):
        with pytest.raises(ReadTimeout):
            controller.change(direction)
    speaker.play_from_queue.assert_not_called()
    assert_session(controller, speaker, server, 2, url)
    controller.change(direction)
    speaker.play_from_queue.assert_called_once_with(target)
    assert_session(controller, speaker, server, target + 1, speaker.queue[target])


@pytest.mark.parametrize('change', ['shifted', 'replaced', 'missing'])
def test_queue_jump_refuses_unverified_destination(jump, change):
    controller, speaker, server, direction, target = jump
    url = controller.owned_url
    if change == 'shifted':
        if direction == 'next':
            speaker.queue.pop(0)
            speaker.queue.append(FOREIGN)
        else:
            speaker.queue.insert(0, FOREIGN)
    elif change == 'replaced':
        speaker.queue[target] = FOREIGN
    else:
        # Model an absent destination while the current URI still reports our track.
        speaker.get_queue.side_effect = None
        speaker.get_queue.return_value = []
    with pytest.raises(RuntimeError):
        controller.change(direction)
    speaker.play_from_queue.assert_not_called()
    assert_session(controller, speaker, server, 2, url)


@pytest.mark.parametrize('applied', [False, True])
def test_ambiguous_queue_jump_is_observed_not_retried(jump, applied):
    controller, speaker, server, direction, target = jump
    old_url = controller.owned_url
    original = type(speaker).play_from_queue.__get__(speaker)

    def uncertain_play(index):
        if applied:
            original(index)
        raise ReadTimeout('queue PLAY outcome unknown')

    speaker.play_from_queue.side_effect = uncertain_play
    with pytest.raises(ReadTimeout):
        controller.change(direction)
    assert_session(controller, speaker, server, 2, old_url)
    with patch.object(speaker, 'get_current_transport_info',
                      side_effect=ReadTimeout('offline')):
        controller.refresh()
        assert controller._sonos_read_failure_since is not None
        assert_session(controller, speaker, server, 2, old_url)
    # Sonos settles to PLAYING after a successful jump, or stays on the old track.
    speaker.transport = 'PLAYING'
    controller.refresh()
    assert controller._sonos_read_failure_since is None
    assert_session(controller, speaker, server, target + 1 if applied else 2,
                   speaker.queue[target] if applied else old_url)
    speaker.play_from_queue.assert_called_once_with(target)
