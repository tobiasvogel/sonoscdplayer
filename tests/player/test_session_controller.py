"""Streaming resource identity across controller and server lifecycles."""
import re
from unittest.mock import Mock, patch

import pytest

from sonoscdplayer.player.controller import Controller, TrackServerProcess
from sonoscdplayer.player.state import Mode, PlayerState
from test_player_controller import FakeServer
from test_player_sonos_queue import FOREIGN, META, QueuePlayer

HOST = '192.0.2.10'
PORT = 8765
UID = 'TEST_UID'


class RecordingServer(FakeServer):
    def __init__(self):
        super().__init__()
        self.attempts = []

    def start(self, track, **options):
        self.attempts.append((track, dict(options)))
        super().start(track, **options)


@pytest.fixture
def make_controller(monkeypatch):
    monkeypatch.setattr('sonoscdplayer.player.controller.didl_item', lambda didl: didl)

    def make(use_queue):
        speaker, server = QueuePlayer(), RecordingServer()

        def verify(uid):
            assert uid == UID
            if speaker.transport != 'STOPPED':
                raise RuntimeError('not STOPPED')
            return speaker

        controller = Controller(PlayerState().disc_loaded((1, 2, 3)), UID,
                                HOST, PORT, server, verify)
        controller.metadata_snapshot = META
        controller.use_queue = use_queue
        speaker.pause = Mock(side_effect=lambda: setattr(speaker, 'transport', 'PAUSED_PLAYBACK'))
        speaker.play = Mock(side_effect=lambda: setattr(speaker, 'transport', 'PLAYING'))
        return controller, speaker, server

    return make


def confirm(controller, speaker):
    speaker.transport = 'PLAYING'
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING


def start(controller, speaker):
    controller.play()
    confirm(controller, speaker)


def session_of(server, attempt=-1):
    _, options = server.attempts[attempt]
    session = options.get('session_id')
    assert isinstance(session, str)
    assert re.fullmatch(r'[0-9a-f]{32}', session)
    return session


def resource(session, track):
    return f'http://{HOST}:{PORT}/session/{session}/track/{track}.wav'


def assert_resource(controller, speaker, server, track):
    session = session_of(server)
    assert controller.state.selected_track == track
    assert controller.owned_url == speaker.uri == resource(session, track)
    return session


@pytest.mark.parametrize('use_queue', [False, True])
def test_play_uses_same_session_for_server_and_sonos(make_controller, use_queue):
    c, p, s = make_controller(use_queue)
    c.state = c.state.select(2)
    start(c, p)
    session = assert_resource(c, p, s, 2)
    assert s.starts == [2]
    if use_queue:
        urls = [resource(session, n) for n in (1, 2, 3)]
        assert p.uris() == [FOREIGN] + urls
        assert c._queue_block['urls'] == urls
        assert not p.direct
    else:
        assert p.direct == [resource(session, 2)]
        assert p.uris() == [FOREIGN]


@pytest.mark.parametrize('use_queue', [False, True])
def test_stop_and_play_allocate_new_session(make_controller, use_queue):
    c, p, s = make_controller(use_queue)
    start(c, p)
    first = c.owned_url
    c.stop()
    assert not s.alive()
    assert p.uris() == [FOREIGN]
    start(c, p)
    assert len(s.attempts) == 2
    assert session_of(s, 0) != session_of(s, 1)
    assert_resource(c, p, s, 1)
    assert c.owned_url != first


@pytest.mark.parametrize('use_queue', [False, True])
def test_pause_resume_keep_resource(make_controller, use_queue):
    c, p, s = make_controller(use_queue)
    start(c, p)
    first = c.owned_url
    c.pause()
    assert c.state.mode == Mode.PAUSED
    assert c.owned_url == first
    c.resume()
    assert c.state.mode == Mode.PLAYING
    assert c.owned_url == first
    assert s.starts == [1]
    p.pause.assert_called_once_with()
    p.play.assert_called_once_with()
    assert_resource(c, p, s, 1)


def test_queue_jumps_and_external_track_change_keep_session(make_controller):
    c, p, s = make_controller(True)
    start(c, p)
    c.change('next')
    confirm(c, p)
    assert_resource(c, p, s, 2)
    c.change('previous')
    confirm(c, p)
    assert_resource(c, p, s, 1)
    p.uri = p.uris()[3]
    c.refresh()
    assert_resource(c, p, s, 3)
    assert s.starts == [1]
    assert p.stops == 0


def test_direct_jumps_allocate_fresh_resources(make_controller):
    c, p, s = make_controller(False)
    start(c, p)
    first = c.owned_url
    c.change('next')
    confirm(c, p)
    assert_resource(c, p, s, 2)
    c.change('previous')
    confirm(c, p)
    assert_resource(c, p, s, 1)
    assert s.starts == [1, 2, 1]
    assert len({session_of(s, i) for i in range(3)}) == 3
    assert c.owned_url != first


def test_direct_natural_end_allocates_fresh_session(make_controller):
    c, p, s = make_controller(False)
    start(c, p)
    c._display_progress = {'elapsed_seconds': 197, 'duration_seconds': 200}
    p.transport = 'STOPPED'
    c.refresh()
    assert c.state.mode == Mode.STARTING
    assert s.starts == [1, 2]
    assert session_of(s, 0) != session_of(s, 1)
    assert_resource(c, p, s, 2)


def test_failed_sonos_play_does_not_reuse_session(make_controller):
    c, p, s = make_controller(False)
    with patch.object(p, 'play_uri', side_effect=OSError('play failed')):
        with pytest.raises(OSError, match='play failed'):
            c.play()
    assert c.state.mode == Mode.ERROR
    assert not s.alive() and c.owned_url is None
    c.reset()
    c.play(UID)
    confirm(c, p)
    assert len(s.attempts) == 2
    assert session_of(s, 0) != session_of(s, 1)
    assert_resource(c, p, s, 1)


@pytest.mark.parametrize('use_queue', [False, True])
def test_new_controller_does_not_reuse_resource(make_controller, use_queue):
    c, p, s = make_controller(use_queue)
    start(c, p)
    first = c.owned_url
    c.stop()
    new_c, new_p, new_s = make_controller(use_queue)
    start(new_c, new_p)
    assert_resource(new_c, new_p, new_s, 1)
    assert new_c.owned_url != first
    assert session_of(s) != session_of(new_s)


def test_server_process_passes_session_to_child():
    session = 'a' * 32
    process = Mock()
    process.poll.return_value = None
    process.stdout = iter(['Serving http://0.0.0.0:8765/track/1.wav\n',
                           'READER started\n'])
    popen = Mock(return_value=process)
    server = TrackServerProcess('.', '/dev/sr0', 'config.toml', PORT,
                                popen=popen, startup_prebuffer=False)
    try:
        server.start(1, session_id=session)
        argv = popen.call_args.args[0]
        assert argv.count('--session-id') == 1
        assert argv[argv.index('--session-id') + 1] == session
    finally:
        server.close()
