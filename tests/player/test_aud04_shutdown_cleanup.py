"""AUD-04a: a failed Sonos stop must not skip local resource cleanup."""
from unittest.mock import Mock

import pytest

from sonoscdplayer.player.service import _close_runtime


def test_shutdown_failure_still_closes_controller_and_drive_power():
    controller = Mock()
    controller.shutdown_owned.side_effect = ConnectionError('Sonos unavailable')
    power = Mock()
    with pytest.raises(ConnectionError, match='Sonos unavailable'):
        _close_runtime(controller, power, 'stop_owned')
    controller.close.assert_called_once_with()
    power.close.assert_called_once_with()


def test_controller_close_failure_still_closes_drive_power():
    controller = Mock()
    controller.shutdown_owned.return_value = {'action': 'stopped'}
    controller.close.side_effect = OSError('local close failed')
    power = Mock()
    with pytest.raises(OSError, match='local close failed'):
        _close_runtime(controller, power, 'stop_owned')
    power.close.assert_called_once_with()


def test_successful_shutdown_closes_everything():
    controller = Mock()
    controller.shutdown_owned.return_value = {'action': 'not_stopped'}
    power = Mock()
    _close_runtime(controller, power, 'leave_playing')
    controller.shutdown_owned.assert_called_once_with('leave_playing')
    controller.close.assert_called_once_with()
    power.close.assert_called_once_with()
