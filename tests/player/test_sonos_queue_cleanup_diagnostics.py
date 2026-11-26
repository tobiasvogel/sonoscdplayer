"""Report best-effort queue cleanup without retrying uncertain Sonos writes."""
from unittest.mock import patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.controller import Controller
from sonoscdplayer.player.state import Mode, PlayerState
from test_aud05a_sonos_transient_read_characterization import _playing_controller
from test_sonos_queue_transition_reads import QueuePlayer, _queued_controller
from test_player_controller import FakeServer


CLEANUP = 'sonoscdplayer.player.controller.remove_queue_block'


def _assert_released(controller, server, mode):
    assert controller.state.mode == mode
    assert controller.owned_url is None
    assert controller.player is None
    assert controller._queue_block is None
    assert not server.alive()


@pytest.mark.parametrize('removed', [3, 2, 0])
def test_stop_reports_confirmed_queue_removals(capsys, removed):
    controller, speaker, server = _queued_controller()
    block = controller._queue_block
    capsys.readouterr()
    with patch(CLEANUP, return_value=removed) as cleanup:
        controller.stop()
    _assert_released(controller, server, Mode.STOPPED)
    cleanup.assert_called_once_with(speaker, block)
    assert speaker.stops == 1
    assert speaker.queue_plays == [0]
    log = capsys.readouterr().out
    assert f'{removed}/3 removals confirmed' in log
    assert ('queue cleanup incomplete' in log) == (removed != 3)


def test_cleanup_exception_does_not_turn_owned_stop_into_failure(capsys):
    controller, speaker, server = _queued_controller()
    block = controller._queue_block
    capsys.readouterr()
    with patch(CLEANUP, side_effect=RuntimeError('cleanup unavailable')) as cleanup:
        controller.stop()
    _assert_released(controller, server, Mode.STOPPED)
    cleanup.assert_called_once_with(speaker, block)
    assert speaker.stops == 1
    assert speaker.queue_plays == [0]
    assert 'queue cleanup' in capsys.readouterr().out


def test_partial_queue_start_failure_reports_cleanup_without_masking_error(capsys):
    speaker = QueuePlayer()
    server = FakeServer()
    controller = Controller(PlayerState().disc_loaded((1, 2, 3)),
                            speaker.uid, '192.0.2.10', 8765, server,
                            verify=lambda uid: speaker)
    add = speaker.add_to_queue
    error = ReadTimeout('second queue addition unavailable')

    def add_one_then_fail(item):
        if speaker.queue:
            raise error
        return add(item)

    with (patch('sonoscdplayer.player.controller.queue_allowed', return_value=True),
          patch('sonoscdplayer.player.controller.track_didl',
                side_effect=lambda url, *args, **kwargs: url),
          patch('sonoscdplayer.player.controller.didl_item', side_effect=lambda item: item),
          patch.object(speaker, 'add_to_queue', side_effect=add_one_then_fail),
          patch(CLEANUP, return_value=0) as cleanup):
        with pytest.raises(ReadTimeout) as exc:
            controller.play()
    assert exc.value is error
    _assert_released(controller, server, Mode.ERROR)
    cleanup.assert_called_once()
    target, block = cleanup.call_args.args
    assert target is speaker
    assert block['urls'] == speaker.queue
    assert len(block['urls']) == 1
    assert speaker.queue_plays == [] and speaker.stops == 0
    log = capsys.readouterr().out
    assert 'queue cleanup incomplete' in log
    assert '0/1 removals confirmed' in log


def test_new_play_does_not_retry_old_block_after_failed_cleanup(capsys):
    controller, speaker, server = _queued_controller()
    old_block = controller._queue_block
    with patch(CLEANUP, return_value=0) as cleanup:
        controller.stop()
        _assert_released(controller, server, Mode.STOPPED)
        controller.use_queue = False
        controller.play()
        speaker.transport = 'PLAYING'
        controller.refresh()
        assert controller.state.mode == Mode.PLAYING
        assert controller._queue_block is None
        assert server.alive()
        controller.stop()
        _assert_released(controller, server, Mode.STOPPED)
    cleanup.assert_called_once_with(speaker, old_block)
    assert speaker.stops == 2
    assert speaker.queue_plays == [0]
    assert server.starts == [1, 1]


def test_direct_stream_stop_does_not_attempt_queue_cleanup(capsys):
    controller, speaker, server = _playing_controller()
    capsys.readouterr()
    with patch(CLEANUP) as cleanup:
        controller.stop()
    _assert_released(controller, server, Mode.STOPPED)
    cleanup.assert_not_called()
    assert speaker.stops == 1
    assert 'queue cleanup' not in capsys.readouterr().out
