"""Bound the device-description read before queue writes."""
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.controller import Controller
from sonoscdplayer.player.state import Mode, PlayerState
from sonoscdplayer.sonos.network_config import configure_request_timeout
from test_player_controller import FakeServer
from test_sonos_queue_transition_reads import QueuePlayer


class ColdQueuePlayer(QueuePlayer):
    """Model SoCo's implicit unbounded read on a cold speaker-info cache."""
    def __init__(self):
        super().__init__()
        self.speaker_info = {}
        self.events = []
        self.info_error = None
        self.empty_info = False

    def get_speaker_info(self, refresh=False, timeout=None):
        self.events.append(('info', timeout))
        if self.info_error is not None:
            raise self.info_error
        if not self.empty_info:
            self.speaker_info = {'uid': self.uid}
        return self.speaker_info

    def add_to_queue(self, item):
        self.events.append(('add', item))
        return super().add_to_queue(item)

    def play_from_queue(self, index):
        if not self.speaker_info:
            self.get_speaker_info()
        self.events.append(('play', index))
        return super().play_from_queue(index)

    def get_queue(self, start, max_items):
        self.events.append(('browse', start))
        return [SimpleNamespace(resources=[SimpleNamespace(uri=url)])
                for url in self.queue[start:start + max_items]]


@pytest.fixture(params=[20.0, 7.0], ids=['default', 'configured'])
def timeout(request, monkeypatch):
    # No real SoCo dependency or network access is required.
    soco = ModuleType('soco')
    soco.config = ModuleType('soco.config')
    soco.config.REQUEST_TIMEOUT = 20.0
    monkeypatch.setitem(sys.modules, 'soco', soco)
    monkeypatch.setitem(sys.modules, 'soco.config', soco.config)
    settings = {} if request.param == 20.0 else {
        'sonos': {'request_timeout_seconds': request.param}}
    assert configure_request_timeout(settings) == request.param
    return request.param


@pytest.fixture
def session(timeout):
    speaker = ColdQueuePlayer()
    server = FakeServer()
    controller = Controller(PlayerState().disc_loaded((1, 2, 3)),
                            speaker.uid, '192.0.2.10', 8765, server,
                            verify=lambda uid: speaker)
    with (patch('sonoscdplayer.player.controller.queue_allowed', return_value=True),
          patch('sonoscdplayer.player.controller.track_didl',
                side_effect=lambda url, *args, **kwargs: url),
          patch('sonoscdplayer.player.controller.didl_item', side_effect=lambda item: item)):
        yield controller, speaker, server


def start_middle(session):
    controller, speaker, server = session
    speaker.speaker_info = {'uid': speaker.uid}
    controller.play()
    speaker.uri = speaker.queue[1]
    speaker.transport = 'PLAYING'
    controller.refresh()
    assert controller.state.selected_track == 2
    assert controller.state.mode == Mode.PLAYING
    speaker.events.clear()
    return controller, speaker, server


def test_cold_start_reads_info_before_queue_writes(session, timeout):
    controller, speaker, server = session
    controller.play()
    assert speaker.events[0] == ('info', timeout)
    assert [event[0] for event in speaker.events] == ['info', 'add', 'add', 'add', 'play']
    assert speaker.queue_plays == [0]
    assert controller.state.mode == Mode.STARTING and server.alive()


@pytest.mark.parametrize('failure', ['timeout', 'empty'])
def test_start_without_info_does_not_modify_queue(session, failure):
    controller, speaker, server = session
    speaker.queue = ['http://foreign.example/track']
    if failure == 'timeout':
        speaker.info_error = ReadTimeout('device description unavailable')
    else:
        speaker.empty_info = True
    with pytest.raises(ReadTimeout if failure == 'timeout' else RuntimeError):
        controller.play()
    assert speaker.queue == ['http://foreign.example/track']
    assert [event[0] for event in speaker.events] == ['info']
    assert speaker.queue_plays == [] and speaker.stops == 0
    assert controller.state.mode == Mode.ERROR
    assert controller.owned_url is None and not server.alive()


@pytest.mark.parametrize('direction,target', [('next', 2), ('previous', 0)])
def test_cold_jump_reads_info_before_destination_check(session, timeout, direction, target):
    controller, speaker, server = start_middle(session)
    speaker.speaker_info = {}
    controller.change(direction)
    assert speaker.events == [('info', timeout), ('browse', target), ('play', target)]
    assert controller.state.selected_track == target + 1
    assert controller.owned_url == speaker.queue[target] and server.alive()


@pytest.mark.parametrize('direction', ['next', 'previous'])
@pytest.mark.parametrize('failure', ['timeout', 'empty'])
def test_failed_info_read_preserves_playing_session(session, direction, failure):
    controller, speaker, server = start_middle(session)
    old_url = controller.owned_url
    old_queue = list(speaker.queue)
    speaker.speaker_info = {}
    if failure == 'timeout':
        speaker.info_error = ReadTimeout('device description unavailable')
    else:
        speaker.empty_info = True
    with pytest.raises(ReadTimeout if failure == 'timeout' else RuntimeError):
        controller.change(direction)
    assert [event[0] for event in speaker.events] == ['info']
    assert speaker.queue_plays == [0] and speaker.queue == old_queue
    assert controller.state.mode == Mode.PLAYING and controller.state.selected_track == 2
    assert controller.owned_url == old_url and server.alive()
    assert server.starts == [1] and speaker.stops == 0


def test_cached_info_needs_no_additional_read(session):
    controller, speaker, server = start_middle(session)
    controller.change('next')
    assert speaker.events == [('browse', 2), ('play', 2)]
    assert controller.state.selected_track == 3 and server.alive()
