"""Passive timing must never change the result of an operation."""
from unittest.mock import Mock

import pytest

from sonoscdplayer.sonos import network_timing
from sonoscdplayer.sonos.network_timing import TimingDiagnostics


@pytest.mark.parametrize('settings', [{}, {'sonos': {}}])
def test_defaults(settings):
    timing = TimingDiagnostics.from_config(settings)
    assert timing.enabled is False
    assert timing.threshold == 1.0


@pytest.mark.parametrize('threshold', [0, 0.5, 1, 60])
def test_valid_settings(threshold):
    timing = TimingDiagnostics.from_config({'sonos': {
        'timing_diagnostics': True, 'timing_threshold_seconds': threshold}})
    assert timing.enabled is True
    assert timing.threshold == float(threshold)


@pytest.mark.parametrize('value', [1, 'true', None])
def test_invalid_switch(value):
    with pytest.raises(ValueError, match='sonos.timing_diagnostics'):
        TimingDiagnostics.from_config({'sonos': {'timing_diagnostics': value}})


@pytest.mark.parametrize('value', [True, '1', None, -1, 61, float('nan'), float('inf')])
def test_invalid_threshold(value):
    with pytest.raises(ValueError, match='sonos.timing_threshold_seconds'):
        TimingDiagnostics.from_config({'sonos': {'timing_threshold_seconds': value}})


def test_invalid_sonos_section():
    with pytest.raises(ValueError, match='must be a table'):
        TimingDiagnostics.from_config({'sonos': []})


def test_disabled_does_not_read_clock_or_log(capsys):
    clock = Mock(side_effect=AssertionError('clock must not be read'))
    timing = TimingDiagnostics(clock=clock)
    action = Mock(return_value={'ok': False})
    request = {'command': 'pause'}
    assert timing.command(action, request) is action.return_value
    action.assert_called_once_with(request)
    assert timing.measure('refresh', lambda: 42) == 42
    clock.assert_not_called()
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('elapsed, logged', [(0.1, False), (1.0, True), (2.5, True)])
def test_refresh_threshold(elapsed, logged, capsys):
    timing = TimingDiagnostics(True, clock=Mock(side_effect=[0, elapsed]))
    action = Mock(return_value=object())
    assert timing.measure('refresh', action) is action.return_value
    action.assert_called_once_with()
    out = capsys.readouterr().out
    assert bool(out) is logged
    if logged:
        assert 'operation=refresh' in out and 'outcome=returned' in out
        assert f'elapsed_seconds={elapsed:.3f}' in out


def test_rejected_command_logged_even_below_threshold(capsys):
    timing = TimingDiagnostics(True, clock=Mock(side_effect=[0, 0.1]))
    reply = {'ok': False, 'error': 'private detail'}
    action = Mock(return_value=reply)
    request = {'command': ' PAUSE ', 'uid': 'private identifier'}
    assert timing.command(action, request) is reply
    action.assert_called_once_with(request)
    out = capsys.readouterr().out
    assert 'command=pause' in out and 'outcome=rejected' in out
    assert 'private' not in out


def test_unknown_command_never_logged_verbatim(capsys):
    timing = TimingDiagnostics(True, threshold=0, clock=Mock(side_effect=[0, 0]))
    timing.command(lambda request: {'ok': True}, {'command': 'private text'})
    out = capsys.readouterr().out
    assert 'command=other' in out and 'outcome=ok' in out
    assert 'private' not in out


def test_exception_propagated_without_message_or_retry(capsys):
    timing = TimingDiagnostics(True, clock=Mock(side_effect=[0, 0.01]))
    error = TimeoutError('private detail')
    action = Mock(side_effect=error)
    with pytest.raises(TimeoutError) as caught:
        timing.measure('refresh', action)
    assert caught.value is error
    action.assert_called_once_with()
    out = capsys.readouterr().out
    assert 'outcome=TimeoutError' in out
    assert 'private' not in out


def test_startup_reports_effective_timeout(monkeypatch, capsys):
    monkeypatch.setattr(network_timing, 'version', lambda package: '0.32.1')
    TimingDiagnostics().log_startup(20.0)
    out = capsys.readouterr().out
    assert 'version=0.32.1 request_timeout_seconds=20' in out
    assert 'enabled=False threshold_seconds=1' in out


def test_offline_start_does_not_look_up_version(monkeypatch, capsys):
    lookup = Mock(side_effect=AssertionError('must not look up absent SoCo'))
    monkeypatch.setattr(network_timing, 'version', lookup)
    TimingDiagnostics().log_startup(None)
    lookup.assert_not_called()
    assert 'enabled=False' in capsys.readouterr().out


def test_version_lookup_failure_is_nonfatal(monkeypatch, capsys):
    monkeypatch.setattr(network_timing, 'version', Mock(side_effect=OSError('private')))
    TimingDiagnostics().log_startup(20.0)
    out = capsys.readouterr().out
    assert 'version=unknown' in out and 'private' not in out


def test_diagnostic_write_failure_does_not_change_result(monkeypatch):
    monkeypatch.setattr(network_timing, 'print', Mock(side_effect=BrokenPipeError), raising=False)
    timing = TimingDiagnostics(True, threshold=0, clock=Mock(side_effect=[0, 1]))
    assert timing.measure('refresh', lambda: 42) == 42
