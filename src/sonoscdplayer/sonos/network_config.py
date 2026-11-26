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
    """Configure SoCo if installed; return None if the optional library is absent."""
    timeout = request_timeout_from_config(settings)
    if soco_config is None:
        try:
            from soco import config as soco_config
        except ModuleNotFoundError as exc:
            if exc.name != 'soco':
                raise
            print('[sonos] SoCo unavailable; request timeout not applied', flush=True)
            return None
    soco_config.REQUEST_TIMEOUT = timeout
    return timeout


def ensure_queue_speaker_info(player):
    """Populate SoCo's cache before its implicit, unbounded queue-play read.

    Alternative player implementations without this cache do not need it.
    A failed or empty read must prevent queue writes, not trigger a retry.
    """
    if not hasattr(player, 'speaker_info') or player.speaker_info:
        return
    from soco import config as soco_config
    player.get_speaker_info(timeout=soco_config.REQUEST_TIMEOUT)
    if not player.speaker_info:
        raise RuntimeError('Sonos speaker information unavailable')
