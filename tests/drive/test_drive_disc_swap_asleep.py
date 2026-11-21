"""A CD swapped with the drive's own eject button while the drive slept."""
from types import SimpleNamespace

from disc_monitor_fakes import CDS_DISC_OK, FakeController, make_monitor

from sonoscdplayer.drive.disc_state import DiscMode


def toc(*numbers):
    return SimpleNamespace(tracks=tuple(SimpleNamespace(number=n) for n in numbers))


def test_monitor_loads_the_disc_a_wake_found():
    old, new = toc(1, 2), toc(1, 2, 3, 4)
    monitor, counts = make_monitor([CDS_DISC_OK], old)
    controller = FakeController()
    assert monitor.poll(controller)                     # first disc
    assert not monitor.poll(controller)                 # READY: no rescan
    monitor.read_toc = lambda _: (counts.__setitem__('toc', counts['toc'] + 1), new)[1]
    monitor.changed_disc = new                          # set by the wake verification
    assert monitor.poll(controller)
    assert controller.disc_status.mode == DiscMode.READY
    assert monitor.changed_disc is None and counts['toc'] == 2
    assert controller.state.track_numbers == (1, 2, 3, 4)
