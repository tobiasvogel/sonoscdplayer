"""AUD-04d: transient USB identity I/O errors must never crash the player."""
from types import SimpleNamespace

from disc_monitor_fakes import FakeController, fake_lock
from sonoscdplayer.drive.disc_monitor import DiscMonitor
from sonoscdplayer.drive.disc_startup import CDS_DISC_OK
from sonoscdplayer.drive.disc_state import DiscMode, DiscStatus
from sonoscdplayer.player.state import PlayerState


def failing_identity(_device):
    raise OSError('USB device vanished during sysfs read')


def monitor():
    return DiscMonitor('/dev/sr0', ('vendor', 'product', 'serial'),
                       media=lambda _: CDS_DISC_OK,
                       toc=lambda _: SimpleNamespace(tracks=(SimpleNamespace(number=1),)),
                       path_exists=lambda _: True,
                       identity_reader=failing_identity,
                       lock=fake_lock)


def test_idle_poll_revokes_stale_disc_without_raising():
    worker = monitor()
    controller = FakeController()
    controller.disc_status = DiscStatus.ready('/dev/sr0', 1)
    controller.state = PlayerState().disc_loaded((1,))
    assert worker.poll(controller) is True
    assert controller.disc_status.mode == DiscMode.NO_DRIVE
    assert controller.state.track_numbers == ()
    assert worker._disconnected is True


def test_play_validation_blocks_on_usb_identity_error():
    worker = monitor()
    controller = FakeController()
    controller.disc_status = DiscStatus.ready('/dev/sr0', 1)
    controller.state = PlayerState().disc_loaded((1,))
    assert worker.validate_before_play(controller) is False
    assert controller.disc_status.mode == DiscMode.NO_DRIVE


def test_passive_media_check_does_not_raise_on_usb_identity_error():
    worker = monitor()
    assert worker.media_removed() is False


def test_disconnected_monitor_can_attempt_verified_recovery():
    worker = monitor()
    controller = FakeController()
    worker._disconnected = True
    called = []
    worker.recover = lambda identity: (called.append(identity), '/dev/sr0')[1]
    worker.identity_reader = lambda _: worker.identity
    assert worker.poll(controller) is True
    assert called == [worker.identity]
    assert controller.disc_status.mode == DiscMode.LOADING
