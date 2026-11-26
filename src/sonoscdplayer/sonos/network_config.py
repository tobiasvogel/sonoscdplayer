"""Player-process configuration of SoCo's SOAP request timeout."""
from __future__ import annotations

DEFAULT_REQUEST_TIMEOUT_SECONDS = 20.0


def request_timeout_from_config(settings):
    """Read the administrator's finite timeout, rejecting invalid values."""
    sonos = settings.get('sonos', {})
    if not isinstance(sonos, dict):
        raise ValueError('[sonos] must be a table')
    value = sonos.get('request_timeout_seconds', DEFAULT_REQUEST_TIMEOUT_SECONDS)
    if type(value) not in (int, float) or not 1 <= value <= 30:
        raise ValueError('sonos.request_timeout_seconds must be a number in 1..30')
    return float(value)


def configure_request_timeout(settings, *, soco_config=None):
    """Set SoCo's supported process-wide timeout before the worker starts."""
    timeout = request_timeout_from_config(settings)
    if soco_config is None:
        from soco import config as soco_config
    soco_config.REQUEST_TIMEOUT = timeout
    return timeout
