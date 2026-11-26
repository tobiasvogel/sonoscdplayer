"""Instrument actual controller paths without extra Sonos operations."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from sonoscdplayer.player.controller import SONOS_READ_GRACE_SECONDS
from sonoscdplayer.player.state import Mode
from sonoscdplayer.sonos.groups import find_speakers
from sonoscdplayer.sonos.network_timing import TimingDiagnostics
from sonoscdplayer.sonos.speaker_checks import find_speaker
from test_aud05a_sonos_transient_read_characterization import _playing_controller
from test_sonos_queue_transition_reads import _queued_controller


def _lines(capsys):
    return [line for line in capsys.readouterr().out.splitlines()
            if line.startswith('[sonos-timing]')]


@pytest.mark.parametrize('multiple', [False, True])
def test_discovery_measures_original_call_without_extra_reads(multiple, capsys):
    speaker = SimpleNamespace(uid='private-uid')
    discover = Mock(return_value=[speaker])
    timing = TimingDiagnostics(True, 0, clock=lambda: 100)
    if multiple:
        result = timing.measure('command', lambda: find_speakers(
            [speaker.uid], discover=discover, timeout=6))
        assert result == {speaker.uid: speaker}
        discover.assert_called_once_with(timeout=6)
    else:
        assert timing.measure('command', lambda: find_speaker(
            speaker.uid, discover=discover)) is speaker
        discover.assert_called_once_with(timeout=5)
    assert _lines(capsys) == [
        '[sonos-timing] operation=discovery elapsed_seconds=0.000 outcome=returned',
        '[sonos-timing] operation=command elapsed_seconds=0.000 outcome=returned',
    ]


@pytest.mark.parametrize('expire', [False, True])
def test_refresh_grace_timing_does_not_change_state_or_deadline(expire, capsys):
    controller, speaker, server = _playing_controller()
    with patch('sonoscdplayer.player.controller.time.monotonic') as clock:
        timing = TimingDiagnostics(True, 60, clock=clock)
        clock.return_value = 100.0
        capsys.readouterr()
        with patch.object(speaker, 'get_current_transport_info', side_effect=TimeoutError('offline')):
            timing.measure('refresh', controller.refresh)
            clock.return_value = 102.0
            timing.measure('refresh', controller.refresh)
        assert controller.state.mode == Mode.PLAYING
        assert server.alive()
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS if expire else 104.0
        if expire:
            with patch.object(speaker, 'get_current_transport_info', side_effect=TimeoutError('offline')):
                timing.measure('refresh', controller.refresh)
        else:
            timing.measure('refresh', controller.refresh)
    lines = _lines(capsys)
    assert [line for line in lines if 'operation=read_grace_' in line] == [
        '[sonos-timing] operation=read_grace_started elapsed_seconds=0.000',
        (f'[sonos-timing] operation=read_grace_expired elapsed_seconds={SONOS_READ_GRACE_SECONDS:.3f}'
         if expire else '[sonos-timing] operation=read_grace_recovered elapsed_seconds=4.000'),
    ]
    assert controller.state.mode == (Mode.ERROR if expire else Mode.PLAYING)
    assert server.alive() is (not expire)
    assert speaker.stops == 0


def test_refresh_measures_group_and_ownership(capsys):
    controller, speaker, server = _playing_controller()
    timing = TimingDiagnostics(True, 0, clock=lambda: 100)
    capsys.readouterr()
    timing.measure('refresh', controller.refresh)
    lines = _lines(capsys)
    for name in ('follow_group', 'ownership', 'refresh'):
        assert sum(f'operation={name} ' in line for line in lines) == 1
    assert controller.state.mode == Mode.PLAYING and server.alive()
    assert speaker.stops == 0 and server.starts == [1]


@pytest.mark.parametrize('direction, target', [('next', 2), ('previous', 0)])
def test_queue_jump_measures_write_after_ownership_checks(direction, target, capsys):
    controller, speaker, server = _queued_controller()
    speaker.uri = speaker.queue[1]
    controller.refresh()
    speaker.get_queue = Mock(side_effect=lambda start, max_items: [
        SimpleNamespace(resources=[SimpleNamespace(uri=url)])
        for url in speaker.queue[start:start + max_items]
    ])
    speaker.play_from_queue = Mock(wraps=speaker.play_from_queue)
    timing = TimingDiagnostics(True, 0, clock=lambda: 100)
    capsys.readouterr()
    timing.command(lambda request: controller.change(direction), {'command': direction})
    lines = _lines(capsys)
    ownership = [i for i, line in enumerate(lines) if 'operation=ownership ' in line]
    queue_play = [i for i, line in enumerate(lines) if 'operation=queue_play ' in line]
    assert len(ownership) == 2 and len(queue_play) == 1
    assert max(ownership) < queue_play[0]
    speaker.play_from_queue.assert_called_once_with(target)
    assert controller.state.selected_track == target + 1
    assert server.alive() and speaker.stops == 0


def test_queue_start_measures_play_once(capsys):
    timing = TimingDiagnostics(True, 0, clock=lambda: 100)
    controller, speaker, server = timing.measure('command', _queued_controller)
    lines = _lines(capsys)
    assert sum('operation=queue_play ' in line for line in lines) == 1
    assert speaker.queue_plays == [0]
    assert controller.state.mode == Mode.PLAYING and server.alive()
