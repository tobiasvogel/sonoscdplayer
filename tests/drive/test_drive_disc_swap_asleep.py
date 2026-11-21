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


from disc_monitor_fakes import CDS_TRAY_OPEN  # noqa: E402

from sonoscdplayer.drive.idle_policy import IdleDrivePolicy  # noqa: E402
from sonoscdplayer.drive.disc_startup import CDS_NO_DISC  # noqa: E402
from sonoscdplayer.player.service import handle_disc_removal  # noqa: E402
from sonoscdplayer.player.state import Mode, PlayerState  # noqa: E402


class Player(FakeController):
    def __init__(self, mode):
        super().__init__()
        self.state = PlayerState(mode=mode, track_numbers=(1, 2), selected_track=1) \
            if mode != Mode.NO_DISC else PlayerState()
        self.owned_url = 'http://192.0.2.10:8765/track/1.wav' if mode in (Mode.PLAYING, Mode.PAUSED) else None
        self.stops = 0

    def stop(self):
        self.stops += 1
        self.owned_url = None
        self.state = self.state.natural_end()


def power(spun_down=False):
    return SimpleNamespace(idle_policy=IdleDrivePolicy(idle_seconds=120, spun_down=spun_down))


def test_cd_taken_out_during_playback_stops_at_once():
    monitor, counts = make_monitor([CDS_TRAY_OPEN])
    player = Player(Mode.PLAYING)
    assert handle_disc_removal(player, monitor, power(), now=10) == 'stopped'
    assert player.stops == 1 and counts['toc'] == 0          # status only, no TOC read


def test_cd_still_in_during_playback_changes_nothing():
    monitor, _ = make_monitor([CDS_DISC_OK])
    player = Player(Mode.PLAYING)
    assert handle_disc_removal(player, monitor, power(), now=10) == 'present'
    assert player.stops == 0


def test_tray_opened_while_asleep_resets_the_idle_state():
    monitor, _ = make_monitor([CDS_NO_DISC])
    drive = power(spun_down=True)
    assert handle_disc_removal(Player(Mode.STOPPED), monitor, drive, now=10) == 'woke'
    assert not drive.idle_policy.spun_down


def test_stopped_and_awake_is_left_to_the_normal_poll():
    monitor, counts = make_monitor([CDS_TRAY_OPEN])
    assert handle_disc_removal(Player(Mode.STOPPED), monitor, power(), now=10) is None
    assert counts['media'] == 0
