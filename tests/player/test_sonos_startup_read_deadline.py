"""Startup and transient Sonos read failures must have bounded deadlines."""
from unittest.mock import patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.controller import (
    Controller, SONOS_READ_GRACE_SECONDS, STARTUP_LIMIT_SECONDS,
)
from sonoscdplayer.player.state import Mode, PlayerState
from test_player_controller import FakePlayer, FakeServer


@pytest.fixture
def starting():
    speaker, server = FakePlayer(), FakeServer()
    controller = Controller(
        PlayerState().disc_loaded((1, 2, 3)), speaker.uid,
        '192.0.2.10', 8765, server, verify=lambda uid: speaker,
    )
    controller.use_queue = False
    with patch('sonoscdplayer.player.controller.time.monotonic') as clock:
        clock.return_value = 100.0
        controller.play()
        assert controller.state.mode == Mode.STARTING
        assert controller.start_monotonic == 100.0
        yield controller, speaker, server, clock


def _observe(starting, elapsed, failed_method=None):
    controller, speaker, _, clock = starting
    clock.return_value = 100.0 + elapsed
    if failed_method is None:
        controller.refresh()
    else:
        with patch.object(speaker, failed_method,
                          side_effect=ReadTimeout('Sonos read unavailable')):
            controller.refresh()


def _assert_active(starting, mode=Mode.STARTING):
    controller, speaker, server, _ = starting
    assert controller.state.mode == mode
    assert server.alive()
    assert controller.player is speaker
    assert controller.owned_url == speaker.uri
    assert speaker.stops == 0
    assert speaker.plays == [speaker.uri]
    assert server.starts == [1]


def _assert_local_abort(starting):
    controller, speaker, server, _ = starting
    assert controller.state.mode == Mode.ERROR
    assert not server.alive()
    assert controller.owned_url is None and controller.player is None
    assert speaker.stops == 0
    assert speaker.plays == [speaker.uri]
    assert server.starts == [1]


@pytest.mark.parametrize('method', ['get_current_transport_info',
                                    'get_current_track_info'])
def test_late_read_outage_cannot_extend_startup_deadline(starting, method):
    _, speaker, _, _ = starting
    if method == 'get_current_track_info':
        # PLAYING alone is not confirmation: the URI must also be verified.
        speaker.transport = 'PLAYING'
    _observe(starting, STARTUP_LIMIT_SECONDS - 1, method)
    _assert_active(starting)
    _observe(starting, STARTUP_LIMIT_SECONDS - 0.1, method)
    _assert_active(starting)
    _observe(starting, STARTUP_LIMIT_SECONDS, method)
    _assert_local_abort(starting)


def test_first_timeout_at_startup_deadline_aborts_locally(starting):
    _observe(starting, STARTUP_LIMIT_SECONDS - 0.1)
    _assert_active(starting)
    _observe(starting, STARTUP_LIMIT_SECONDS, 'get_current_transport_info')
    _assert_local_abort(starting)


def test_early_outage_expires_at_read_grace_before_startup_deadline(starting):
    _observe(starting, 1, 'get_current_transport_info')
    _observe(starting, 1 + SONOS_READ_GRACE_SECONDS - 0.1,
             'get_current_transport_info')
    _assert_active(starting)
    _observe(starting, 1 + SONOS_READ_GRACE_SECONDS,
             'get_current_transport_info')
    _assert_local_abort(starting)


def test_recovery_before_startup_deadline_confirms_playback(starting):
    controller, speaker, _, _ = starting
    _observe(starting, STARTUP_LIMIT_SECONDS - 2,
             'get_current_transport_info')
    _assert_active(starting)
    speaker.transport = 'PLAYING'
    _observe(starting, STARTUP_LIMIT_SECONDS - 1)
    _assert_active(starting, Mode.PLAYING)
    assert controller.seen_playing
    assert controller._sonos_read_failure_since is None
    # Once confirmed, startup time no longer restricts read grace.
    _observe(starting, STARTUP_LIMIT_SECONDS, 'get_current_transport_info')
    _assert_active(starting, Mode.PLAYING)


def test_alternating_transitioning_and_timeouts_do_not_restart_startup_clock(starting):
    controller, _, _, _ = starting
    for elapsed in (2, 12, 22):
        _observe(starting, elapsed, 'get_current_transport_info')
        _assert_active(starting)
        _observe(starting, elapsed + 1)
        _assert_active(starting)
        assert controller._sonos_read_failure_since is None
        assert controller.start_monotonic == 100.0
    _observe(starting, STARTUP_LIMIT_SECONDS - 1,
             'get_current_transport_info')
    _assert_active(starting)
    _observe(starting, STARTUP_LIMIT_SECONDS)
    _assert_local_abort(starting)


def test_recovery_after_deadline_without_playing_aborts_locally(starting):
    _observe(starting, STARTUP_LIMIT_SECONDS - 1,
             'get_current_transport_info')
    _assert_active(starting)
    _observe(starting, STARTUP_LIMIT_SECONDS + 1)
    _assert_local_abort(starting)
