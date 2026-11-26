"""Distinguish incomplete queue observations from confirmed ownership loss."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.controller import SONOS_READ_GRACE_SECONDS
from sonoscdplayer.player.state import Mode
from test_sonos_queue_transition_reads import _queued_controller, _assert_no_writes


@pytest.fixture
def ended_queue():
    controller, speaker, server = _queued_controller()
    # Observe the final track before Sonos returns to the queue's first entry.
    speaker.uri = speaker.queue[-1]
    controller.refresh()
    assert controller.state.selected_track == 3
    speaker.transport = 'STOPPED'
    speaker.uri = speaker.queue[0]
    speaker.avTransport = SimpleNamespace(GetMediaInfo=Mock(
        return_value={'CurrentURI': 'x-rincon-queue:TEST_UID#0'}))
    speaker.get_queue = Mock(side_effect=lambda start, max_items: [
        SimpleNamespace(resources=[SimpleNamespace(uri=url)])
        for url in speaker.queue[start:start + max_items]
    ])
    # Isolate cleanup from observation; its writes are audited separately.
    with patch('sonoscdplayer.player.controller.remove_queue_block') as cleanup:
        yield controller, speaker, server, cleanup


@pytest.mark.parametrize('method', ['media', 'queue'])
def test_queue_end_read_timeout_preserves_session_then_recovers(ended_queue, method):
    controller, speaker, server, cleanup = ended_queue
    old_url = controller.owned_url
    block = controller._queue_block
    target = speaker.avTransport if method == 'media' else speaker
    name = 'GetMediaInfo' if method == 'media' else 'get_queue'
    with patch.object(target, name, side_effect=ReadTimeout('offline')):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert server.alive() and controller.owned_url == old_url
    assert controller.state.selected_track == 3
    assert controller._queue_block is block
    assert controller._sonos_read_failure_since is not None
    cleanup.assert_not_called()
    _assert_no_writes(speaker, server)

    controller.refresh()
    assert controller.state.mode == Mode.STOPPED
    assert not server.alive() and controller.owned_url is None
    assert controller._queue_block is None
    assert controller._sonos_read_failure_since is None
    cleanup.assert_called_once_with(speaker, block)
    _assert_no_writes(speaker, server)


def test_sustained_queue_end_read_failure_expires(ended_queue):
    controller, speaker, server, cleanup = ended_queue
    with (patch.object(speaker, 'get_queue', side_effect=ReadTimeout('offline')),
          patch('sonoscdplayer.player.controller.time.monotonic') as clock):
        clock.return_value = 100.0
        controller.refresh()
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS - 0.1
        controller.refresh()
        assert controller.state.mode == Mode.PLAYING and server.alive()
        cleanup.assert_not_called()
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS
        controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert not server.alive() and controller.owned_url is None
    cleanup.assert_called_once()
    _assert_no_writes(speaker, server)


@pytest.mark.parametrize('mismatch', ['foreign_source', 'foreign_entry', 'missing_entry'])
def test_queue_end_confirmed_mismatch_fails_closed(ended_queue, mismatch):
    controller, speaker, server, cleanup = ended_queue
    if mismatch == 'foreign_source':
        speaker.avTransport.GetMediaInfo.return_value = {'CurrentURI': 'http://foreign.example/stream'}
    else:
        speaker.get_queue.side_effect = None
        speaker.get_queue.return_value = ([] if mismatch == 'missing_entry' else [
            SimpleNamespace(resources=[SimpleNamespace(uri='http://foreign.example/track')])
        ])
    controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert not server.alive() and controller.owned_url is None
    cleanup.assert_called_once()
    _assert_no_writes(speaker, server)


def test_queue_end_without_read_failure_is_natural_end(ended_queue):
    controller, speaker, server, cleanup = ended_queue
    block = controller._queue_block
    controller.refresh()
    assert controller.state.mode == Mode.STOPPED
    assert not server.alive() and controller.owned_url is None
    assert controller._queue_block is None
    cleanup.assert_called_once_with(speaker, block)
    _assert_no_writes(speaker, server)
