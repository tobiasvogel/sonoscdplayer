"""Physical CD timing takes precedence over Sonos duration reports."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sonoscdplayer.player.controller import Controller
from sonoscdplayer.player.state import Mode, PlayerState
from sonoscdplayer.ui.menu.playback_progress import verified_progress
from test_player_controller import FakeServer
from test_player_sonos_queue import QueuePlayer, FOREIGN


URI = 'http://192.0.2.10:8765/session/' + 'a' * 32 + '/track/1.wav'
OLD_URI = URI.replace('a' * 32, 'b' * 32)


def timing(position, duration, uri=URI):
    return {'uri': uri, 'position': position, 'duration': duration}


@pytest.mark.parametrize('position,reported,physical,expected', [
    ('0:00:20', '0:01:03', 22, {'elapsed_seconds': 20, 'duration_seconds': 22}),
    ('0:00:20', '0:00:21', 86, {'elapsed_seconds': 20, 'duration_seconds': 86}),
    ('0:01:00', '0:00:21', 86, {'elapsed_seconds': 60, 'duration_seconds': 86}),
    ('0:00:23', '0:01:03', 22, None),
    ('0:00:22', '0:00:00', 22, {'elapsed_seconds': 22, 'duration_seconds': 22}),
    ('0:00:20', None, 22, {'elapsed_seconds': 20, 'duration_seconds': 22}),
    ('0:00:20', 'NOT_IMPLEMENTED', 22, {'elapsed_seconds': 20, 'duration_seconds': 22}),
    ('0:00:20', '0:01:03', None, {'elapsed_seconds': 20, 'duration_seconds': 63}),
    ('0:00:20', '0:00:00', None, {'elapsed_seconds': 20, 'duration_seconds': None}),
    ('0:00:23', '0:00:22', None, None),
    ('NOT_IMPLEMENTED', '0:01:03', 22, None),
    ('0:00:60', '0:01:03', 22, None),
])
def test_verified_progress_duration(position, reported, physical, expected):
    assert verified_progress(timing(position, reported), URI,
                             duration_fallback=physical) == expected


@pytest.mark.parametrize('physical', [0, -1, True, 22.0, '22', 360000])
def test_invalid_physical_duration_preserves_reported_duration(physical):
    assert verified_progress(timing('0:00:20', '0:01:03'), URI,
                             duration_fallback=physical) == {
        'elapsed_seconds': 20, 'duration_seconds': 63}


@pytest.mark.parametrize('uri', [OLD_URI, 'http://foreign.example/track/1.wav', ''])
def test_physical_duration_never_substitutes_for_uri_ownership(uri):
    assert verified_progress(timing('0:00:20', '0:01:03', uri), URI,
                             duration_fallback=22) is None


class TimedPlayer(QueuePlayer):
    position = '0:00:00'
    duration = '0:00:00'

    def get_current_track_info(self):
        return timing(self.position, self.duration, self.uri)


def start_playback(position, reported, physical, *, use_queue=False, track=1):
    speaker = TimedPlayer()
    server = FakeServer()

    def verify(_uid):
        if speaker.transport != 'STOPPED':
            raise RuntimeError('not STOPPED')
        return speaker

    controller = Controller(PlayerState().disc_loaded((1, 2, 3)),
                            'TEST_UID', '192.0.2.10', 8765, server, verify)
    controller.use_queue = use_queue
    controller.state = controller.state.select(track)
    if physical is not None:
        durations = {1: physical, 2: 85.51, 3: 19.49}
        durations[track] = physical
        controller.display_toc = SimpleNamespace(get_track=lambda n: SimpleNamespace(
            duration_seconds=durations[n]))
    speaker.position, speaker.duration = position, reported
    with patch('sonoscdplayer.player.controller.didl_item', lambda didl: didl):
        controller.play()
    speaker.transport = 'PLAYING'
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    return controller, speaker, server


@pytest.mark.parametrize('position,reported,physical,advance', [
    ('0:00:20', '0:01:03', 21.72, True),
    ('0:00:20', '0:00:21', 85.51, False),
    ('0:01:24', '0:00:21', 85.51, True),
    ('0:00:23', '0:01:03', 21.72, False),
    ('0:00:10', '0:01:03', 21.72, False),
    ('0:00:20', '0:00:00', 21.72, True),
    ('0:00:20', '0:00:22', None, True),
    ('NOT_IMPLEMENTED', '0:00:22', 21.72, False),
])
def test_direct_stop_uses_verified_physical_end(position, reported, physical, advance):
    controller, speaker, server = start_playback(position, reported, physical)
    speaker.transport = 'STOPPED'
    controller.refresh()
    assert speaker.stops == 0
    assert server.starts == ([1, 2] if advance else [1])
    assert controller.state.selected_track == (2 if advance else 1)
    assert controller.state.mode == (Mode.STARTING if advance else Mode.STOPPED)
    assert server.alive() is advance


def test_queue_follow_uses_new_tracks_physical_duration():
    controller, speaker, server = start_playback('0:00:20', '0:01:03', 21.72,
                                                use_queue=True)
    speaker.uri = speaker.uris()[2]
    speaker.position, speaker.duration = '0:01:00', '0:00:21'
    controller.refresh()
    assert controller.state.selected_track == 2
    assert controller.status_snapshot()['progress'] == {
        'elapsed_seconds': 60, 'duration_seconds': 86}
    assert server.starts == [1]
    assert speaker.stops == 0


def test_queue_stop_does_not_start_another_server():
    controller, speaker, server = start_playback('0:00:20', '0:01:03', 21.72,
                                                use_queue=True)
    speaker.transport = 'STOPPED'
    controller.refresh()
    assert controller.state.mode == Mode.STOPPED
    assert server.starts == [1]
    assert speaker.stops == 0
    assert speaker.uris() == [FOREIGN]


def test_direct_foreign_session_stop_never_advances():
    controller, speaker, server = start_playback('0:00:20', '0:00:22', 21.72)
    speaker.uri = OLD_URI
    speaker.transport = 'STOPPED'
    controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert server.starts == [1]
    assert not server.alive()
    assert speaker.stops == 0
