"""Validate the configured SOAP timeout without contacting Sonos."""
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
