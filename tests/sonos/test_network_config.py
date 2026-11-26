"""Validate the configured SOAP timeout without contacting Sonos."""
import builtins
import sys
from types import SimpleNamespace

import pytest

from sonoscdplayer.sonos.network_config import (
    configure_request_timeout, request_timeout_from_config,
)


@pytest.mark.parametrize('settings', [{}, {'sonos': {}}])
def test_missing_timeout_uses_default(settings):
    assert request_timeout_from_config(settings) == 20.0


@pytest.mark.parametrize('value', [1, 2.5, 5, 20, 30])
def test_valid_timeout_is_applied(value):
    config = SimpleNamespace(REQUEST_TIMEOUT=20.0, ZGT_EVENT_FALLBACK=True)
    assert configure_request_timeout(
        {'sonos': {'request_timeout_seconds': value}}, soco_config=config) == float(value)
    assert config.REQUEST_TIMEOUT == float(value)
    assert type(config.REQUEST_TIMEOUT) is float
    assert config.ZGT_EVENT_FALLBACK is True


@pytest.mark.parametrize('value', [True, False, None, '5', 0, 0.99, 31,
                                   float('nan'), float('inf'), -float('inf')])
def test_invalid_timeout_does_not_modify_soco(value):
    config = SimpleNamespace(REQUEST_TIMEOUT=20.0)
    with pytest.raises(ValueError, match='sonos.request_timeout_seconds'):
        configure_request_timeout(
            {'sonos': {'request_timeout_seconds': value}}, soco_config=config)
    assert config.REQUEST_TIMEOUT == 20.0


def test_invalid_sonos_section_is_rejected():
    with pytest.raises(ValueError, match='must be a table'):
        request_timeout_from_config({'sonos': []})


def test_missing_soco_does_not_prevent_offline_start(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, 'soco', None)
    assert configure_request_timeout({}) is None
    assert 'SoCo unavailable' in capsys.readouterr().out


def test_missing_soco_does_not_bypass_validation(monkeypatch):
    monkeypatch.setitem(sys.modules, 'soco', None)
    with pytest.raises(ValueError, match='sonos.request_timeout_seconds'):
        configure_request_timeout({'sonos': {'request_timeout_seconds': 0}})


def test_broken_soco_dependency_is_not_silenced(monkeypatch):
    original_import = builtins.__import__

    def import_with_missing_dependency(name, *args, **kwargs):
        if name == 'soco':
            raise ModuleNotFoundError('missing dependency', name='soco_dependency')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', import_with_missing_dependency)
    with pytest.raises(ModuleNotFoundError) as exc:
        configure_request_timeout({})
    assert exc.value.name == 'soco_dependency'
