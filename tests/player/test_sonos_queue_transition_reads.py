"""Keep confirmed queue track state across incomplete Sonos observations."""
from unittest.mock import patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.controller import Controller
from sonoscdplayer.player.state import Mode, PlayerState
from test_player_controller import FakePlayer, FakeServer


class QueuePlayer(FakePlayer):
    def __init__(self):
        super().__init__()
        self.queue = []
        self.queue_plays = []

    def add_to_queue(self, item):
        self.queue.append(item)
        return len(self.queue)

    def play_from_queue(self, index):
        self.queue_plays.append(index)
        self.uri = self.queue[index]
        self.transport = 'TRANSITIONING'


def _queued_controller():
    speaker = QueuePlayer()
    server = FakeServer()
    controller = Controller(PlayerState().disc_loaded((1, 2, 3)),
                            speaker.uid, '192.0.2.10', 8765, server,
                            verify=lambda uid: speaker)
    # Exercise the real controller queue startup, without SoCo DIDL parsing.
    with (patch('sonoscdplayer.player.controller.queue_allowed', return_value=True),
          patch('sonoscdplayer.player.controller.track_didl',
                side_effect=lambda url, *args, **kwargs: url),
          patch('sonoscdplayer.player.controller.didl_item', side_effect=lambda item: item)):
        controller.play()
    speaker.transport = 'PLAYING'
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller.state.selected_track == 1
    assert controller._queue_block['urls'] == speaker.queue
    assert server.options['disc'] is True
    return controller, speaker, server


def _assert_no_writes(speaker, server):
    assert speaker.stops == 0
    assert speaker.plays == []
    assert speaker.queue_plays == [0]
    assert server.starts == [1]
    assert len(speaker.queue) == 3


def test_queue_track_read_timeout_before_observation_then_recovers():
    controller, speaker, server = _queued_controller()
    old_url, next_url = speaker.queue[:2]
    speaker.uri = next_url
    with patch.object(speaker, 'get_current_track_info',
                      side_effect=ReadTimeout('track read unavailable')):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller.state.selected_track == 1
    assert controller.owned_url == old_url
    assert controller._sonos_read_failure_since is not None
    assert server.alive()
    controller.refresh()
    assert controller.state.selected_track == 2
    assert controller.owned_url == next_url
    assert controller._sonos_read_failure_since is None
    assert server.alive()
    _assert_no_writes(speaker, server)


@pytest.mark.parametrize('transport, expected_mode', [
    ('PLAYING', Mode.PLAYING), ('PAUSED_PLAYBACK', Mode.PAUSED),
])
def test_queue_track_is_not_committed_before_ownership_confirmation(transport, expected_mode):
    controller, speaker, server = _queued_controller()
    old_url, next_url = speaker.queue[:2]
    speaker.transport = transport
    speaker.uri = next_url
    with patch.object(speaker, 'get_current_track_info', side_effect=[
        {'uri': next_url}, ReadTimeout('follow-up read unavailable'),
    ]):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert server.alive()
    assert controller._sonos_read_failure_since is not None
    _assert_no_writes(speaker, server)
    assert controller.state.selected_track == 1
    assert controller.owned_url == old_url

    controller.refresh()
    assert controller.state.mode == expected_mode
    assert controller.state.selected_track == 2
    assert controller.owned_url == next_url
    assert controller._sonos_read_failure_since is None
    assert server.alive()
    _assert_no_writes(speaker, server)


def test_queue_track_transition_without_failure():
    controller, speaker, server = _queued_controller()
    speaker.uri = speaker.queue[1]
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller.state.selected_track == 2
    assert controller.owned_url == speaker.uri
    assert server.alive()
    _assert_no_writes(speaker, server)


def test_queue_track_race_between_own_uris_recovers_without_restart():
    controller, speaker, server = _queued_controller()
    speaker.uri = speaker.queue[2]
    with patch.object(speaker, 'get_current_track_info', side_effect=[
        {'uri': speaker.queue[1]}, {'uri': speaker.queue[2]},
    ]):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING and server.alive()
    controller.refresh()
    assert controller.state.selected_track == 3
    assert controller.owned_url == speaker.uri
    assert server.alive()
    _assert_no_writes(speaker, server)


def test_queue_foreign_uri_between_reads_fails_closed():
    controller, speaker, server = _queued_controller()
    speaker.uri = 'http://external.example/foreign'
    # Queue cleanup is a separate concern; verify the ownership decision only.
    with (patch.object(speaker, 'get_current_track_info', side_effect=[
              {'uri': speaker.queue[1]}, {'uri': speaker.uri},
          ]), patch('sonoscdplayer.player.controller.remove_queue_block') as cleanup):
        controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert controller.owned_url is None
    assert not server.alive()
    cleanup.assert_called_once()
    _assert_no_writes(speaker, server)
