"""Nested network timing is passive and confined to the worker call."""
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sonoscdplayer.sonos.network_config import ensure_queue_speaker_info
from sonoscdplayer.sonos.network_timing import (
    TimingDiagnostics, measure_network, read_grace_event, timed_network,
)


def test_disabled_and_unscoped_calls_do_not_read_clock_or_log(capsys):
    clock = Mock(side_effect=AssertionError('unexpected clock read'))
    action = Mock(return_value=object())
    timing = TimingDiagnostics(False, 0, clock=clock)
    assert timing.measure('refresh', lambda: measure_network('discovery', action)) is action.return_value
    assert measure_network('discovery', action) is action.return_value
    read_grace_event('read_grace_started', 100)
    assert action.call_count == 2
    clock.assert_not_called()
    assert capsys.readouterr().out == ''


def test_nested_operation_logs_own_duration_and_preserves_result(capsys):
    clock = Mock(side_effect=[0, 1, 3, 4])
    timing = TimingDiagnostics(True, 0, clock=clock)
    action = Mock(return_value=object())
    assert timing.measure('refresh', lambda: measure_network('discovery', action)) is action.return_value
    action.assert_called_once_with()
    assert capsys.readouterr().out.splitlines() == [
        '[sonos-timing] operation=discovery elapsed_seconds=2.000 outcome=returned',
        '[sonos-timing] operation=refresh elapsed_seconds=4.000 outcome=returned',
    ]
    measure_network('discovery', action)
    assert capsys.readouterr().out == ''


def test_nested_failure_is_logged_once_per_scope_and_context_is_restored(capsys):
    error = TimeoutError('private-address')
    action = Mock(side_effect=error)
    timing = TimingDiagnostics(True, 60, clock=lambda: 100)
    with pytest.raises(TimeoutError) as raised:
        timing.measure('refresh', lambda: measure_network('discovery', action))
    assert raised.value is error
    action.assert_called_once_with()
    output = capsys.readouterr().out
    assert output.count('outcome=TimeoutError') == 2
    assert 'private-address' not in output
    measure_network('discovery', lambda: None)
    assert capsys.readouterr().out == ''


def test_context_does_not_leak_to_another_thread(capsys):
    timing = TimingDiagnostics(True, 0, clock=lambda: 100)
    with ThreadPoolExecutor(max_workers=1) as executor:
        timing.measure('refresh', lambda: executor.submit(
            measure_network, 'discovery', lambda: None).result(timeout=2))
    assert 'operation=discovery' not in capsys.readouterr().out


def test_decorator_keeps_arguments_and_return_value(capsys):
    action = Mock(return_value=object())

    @timed_network('ownership')
    def check(*args, **kwargs):
        return action(*args, **kwargs)

    timing = TimingDiagnostics(True, 0, clock=lambda: 100)
    assert timing.measure('refresh', lambda: check('private-uid', expected='PLAYING')) is action.return_value
    action.assert_called_once_with('private-uid', expected='PLAYING')
    output = capsys.readouterr().out
    assert 'operation=ownership' in output
    assert 'private-uid' not in output


def test_grace_events_ignore_threshold_and_use_existing_deadline_observation(capsys):
    clock = Mock(side_effect=[100, 104, 110])
    timing = TimingDiagnostics(True, 60, clock=clock)

    def events():
        read_grace_event('read_grace_started', 100, now=100)
        read_grace_event('read_grace_recovered', 100)
        read_grace_event('read_grace_expired', 100, now=110)

    timing.measure('refresh', events)
    assert clock.call_count == 3
    assert capsys.readouterr().out.splitlines() == [
        '[sonos-timing] operation=read_grace_started elapsed_seconds=0.000',
        '[sonos-timing] operation=read_grace_recovered elapsed_seconds=4.000',
        '[sonos-timing] operation=read_grace_expired elapsed_seconds=10.000',
    ]


@pytest.mark.parametrize('case', ['cached', 'cold', 'empty', 'timeout'])
def test_speaker_info_only_measures_actual_cache_population(case, monkeypatch, capsys):
    config = SimpleNamespace(REQUEST_TIMEOUT=7.0)
    monkeypatch.setitem(sys.modules, 'soco', SimpleNamespace(config=config))
    player = SimpleNamespace(speaker_info={'name': 'private-name'} if case == 'cached' else {})

    def populate(**kwargs):
        if case == 'cold':
            player.speaker_info = {'name': 'private-name'}
        elif case == 'timeout':
            raise TimeoutError('private-address')

    player.get_speaker_info = Mock(side_effect=populate)
    timing = TimingDiagnostics(True, 0, clock=lambda: 100)
    call = lambda: timing.measure('command', lambda: ensure_queue_speaker_info(player))
    if case in ('empty', 'timeout'):
        with pytest.raises(RuntimeError if case == 'empty' else TimeoutError):
            call()
    else:
        call()
    output = capsys.readouterr().out
    if case == 'cached':
        player.get_speaker_info.assert_not_called()
        assert 'operation=speaker_info' not in output
    else:
        player.get_speaker_info.assert_called_once_with(timeout=7.0)
        assert output.count('operation=speaker_info') == 1
        expected = {'cold': 'returned', 'empty': 'RuntimeError', 'timeout': 'TimeoutError'}[case]
        assert f'operation=speaker_info elapsed_seconds=0.000 outcome={expected}' in output
    assert 'private-name' not in output and 'private-address' not in output
