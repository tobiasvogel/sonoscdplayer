"""AUD-04f: a CD inserted after boot activates power management safely."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sonoscdplayer.drive.disc_state import DiscMode, DiscStatus
from sonoscdplayer.player.service import _start_power_if_ready
from sonoscdplayer.player.state import PlayerState


class Controller:
    def __init__(self, mode):
        self.disc_status = DiscStatus(mode, '/dev/sr0')
        self.state = (PlayerState().disc_loaded((1, 2))
                      if mode is DiscMode.READY else PlayerState())
        self.owned_url = None
        self.player = None
        self._joined = {}
        self._group_uncertain = False
        self.server = Mock()
        self.server.alive.return_value = False
        self.server.reader_active.return_value = False


@contextmanager
def lock(_device, *, blocking=False):
    assert blocking is False
    yield True


@contextmanager
def busy_lock(_device, *, blocking=False):
    yield False


def power():
    manager = Mock(device='/dev/sr0', _started=False)
    manager.start.side_effect = lambda: setattr(manager, '_started', True)
    return manager


def test_not_started_without_verified_disc():
    manager = power()
    with patch('sonoscdplayer.player.service.drive_lock', lock):
        assert not _start_power_if_ready(manager, Controller(DiscMode.NO_DISC), None)
    manager.start.assert_not_called()


def test_cdtext_and_busy_lock_defer_power_commands():
    manager = power()
    controller = Controller(DiscMode.READY)
    monitor = SimpleNamespace(cdtext_pending=True)
    with patch('sonoscdplayer.player.service.drive_lock', lock):
        assert not _start_power_if_ready(manager, controller, monitor)
    monitor.cdtext_pending = False
    with patch('sonoscdplayer.player.service.drive_lock', busy_lock):
        assert not _start_power_if_ready(manager, controller, monitor)
    manager.start.assert_not_called()
    with patch('sonoscdplayer.player.service.drive_lock', lock):
        assert _start_power_if_ready(manager, controller, monitor)
        assert not _start_power_if_ready(manager, controller, monitor)
    manager.start.assert_called_once_with()


def test_active_reader_never_triggers_power_probe():
    manager = power()
    controller = Controller(DiscMode.READY)
    controller.server.reader_active.return_value = True
    with patch('sonoscdplayer.player.service.drive_lock', lock):
        assert not _start_power_if_ready(manager, controller, None)
    manager.start.assert_not_called()
