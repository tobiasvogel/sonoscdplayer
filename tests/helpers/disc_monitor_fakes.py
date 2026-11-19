"""Fake controller and DiscMonitor factory shared by drive and metadata tests."""
import contextlib

from sonoscdplayer.drive.disc_monitor import DiscMonitor
from sonoscdplayer.drive.disc_startup import CDS_DISC_OK, CDS_TRAY_OPEN  # noqa: F401  (re-exported)
from sonoscdplayer.drive.disc_state import DiscMode, DiscStatus
from sonoscdplayer.player.state import PlayerState


class Server:
    def __init__(self):
        self.running = False
        self.reader = False

    def alive(self):
        return self.running

    def reader_active(self):
        return self.reader


class FakeController:
    def __init__(self):
        self.server = Server()
        self.state = PlayerState()
        self.disc_status = DiscStatus(DiscMode.NO_DISC, '/dev/sr0')
        self.owned_url = None
        self.player = None
        self._joined = {}
        self._group_uncertain = False


@contextlib.contextmanager
def fake_lock(_device, *, blocking=False):
    assert not blocking
    yield True


@contextlib.contextmanager
def unavailable_lock(_device, *, blocking=False):
    assert not blocking
    yield False


def make_monitor(status, toc=None, *, lock=fake_lock):
    counts = {'media': 0, 'toc': 0}
    def media(_):
        counts['media'] += 1
        return status[0]
    def read(_):
        counts['toc'] += 1
        if toc is None:
            raise ValueError('invalid audio CD')
        return toc
    monitor = DiscMonitor('/dev/sr0', None, media=media, toc=read,
                          path_exists=lambda _: True, identity_reader=lambda _: None,
                          lock=lock)
    return monitor, counts
