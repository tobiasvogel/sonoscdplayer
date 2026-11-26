"""Exercise the serialized player worker through its real local control socket."""
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.service import MAX_WAITING_COMMANDS, serve
from sonoscdplayer.player.state import Mode
from test_aud05a_sonos_transient_read_characterization import _playing_controller
from test_player_service import wait_until_listening
from test_sonos_service_network_safety import assert_snapshot


@pytest.fixture
def live_service():
    """Release every simulated blocking call before joining the service."""
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / 'control.sock')
        stop = threading.Event()
        release = threading.Event()
        errors = []
        threads = []

        def start(controller):
            def run():
                try:
                    serve(controller, path, poll_seconds=0.02, stop_event=stop)
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=run, daemon=True)
            threads.append(thread)
            thread.start()
            wait_until_listening(path)
            return path

        # Dashboard rendering is unrelated to the control worker under test.
        with patch('sonoscdplayer.player.service.DashboardPublisher'):
            try:
                yield start, release
            finally:
                release.set()
                stop.set()
                for thread in threads:
                    thread.join(timeout=5)
                    assert not thread.is_alive(), 'player service did not stop'
                assert not errors


def send(path, command):
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.settimeout(5)
        conn.connect(path)
        conn.sendall((json.dumps({'command': command}) + '\n').encode())
        return conn
    except BaseException:
        conn.close()
        raise


def receive(conn):
    with conn.makefile('rb') as stream:
        return json.loads(stream.readline())


def rpc(path, command):
    with send(path, command) as conn:
        return receive(conn)


def wait_for_mode(path, mode):
    deadline = time.monotonic() + 5
    while True:
        reply = rpc(path, 'status')
        if reply['state']['player']['mode'] == mode.value:
            return reply
        assert time.monotonic() < deadline, 'status was not updated after recovery'
        time.sleep(0.01)


def test_blocked_refresh_keeps_status_responsive_and_bounds_pending_commands(live_service):
    start, release = live_service
    controller, speaker, server = _playing_controller()
    entered = threading.Event()
    original_read = speaker.get_current_transport_info
    speaker.pause = Mock(side_effect=lambda: setattr(speaker, 'transport', 'PAUSED_PLAYBACK'))

    def blocked_read():
        if not entered.is_set():
            entered.set()
            assert release.wait(10), 'test did not release blocked read'
            raise ReadTimeout('temporary network outage')
        return original_read()

    speaker.get_current_transport_info = Mock(side_effect=blocked_read)
    path = start(controller)
    assert entered.wait(5), 'worker did not enter Sonos read'
    try:
        reply = rpc(path, 'status')
        assert reply['ok'] is True
        assert_snapshot(reply, Mode.PLAYING)
        assert not release.is_set()
        assert speaker.get_current_transport_info.call_count == 1

        with ExitStack() as clients:
            abandoned = [clients.enter_context(send(path, 'pause'))
                         for _ in range(MAX_WAITING_COMMANDS - 1)]
            # This live command is a FIFO barrier behind all abandoned commands.
            barrier = clients.enter_context(send(path, 'help'))
            reply = rpc(path, 'pause')
            assert reply['ok'] is False
            assert 'busy' in reply['error']
            speaker.pause.assert_not_called()
            assert_snapshot(rpc(path, 'status'), Mode.PLAYING)
            for conn in abandoned:
                conn.close()
            release.set()
            assert receive(barrier)['ok'] is True
            speaker.pause.assert_not_called()
        # The worker still accepts a fresh command after the outage.
        assert rpc(path, 'pause')['ok'] is True
        assert_snapshot(rpc(path, 'status'), Mode.PAUSED)
        speaker.pause.assert_called_once_with()
        assert server.alive() and server.starts == [1] and speaker.stops == 0
    finally:
        release.set()


@pytest.mark.parametrize('applied', [False, True])
def test_disconnected_client_does_not_retry_started_resume(live_service, applied):
    start, release = live_service
    controller, speaker, server = _playing_controller()
    speaker.transport = 'PAUSED_PLAYBACK'
    controller.refresh()
    assert controller.state.mode == Mode.PAUSED
    entered = threading.Event()

    def uncertain_play():
        entered.set()
        assert release.wait(10), 'test did not release blocked write'
        if applied:
            speaker.transport = 'PLAYING'
        raise ReadTimeout('PLAY outcome unknown')

    speaker.play = Mock(side_effect=uncertain_play)
    path = start(controller)
    try:
        with send(path, 'resume'):
            assert entered.wait(5), 'worker did not enter Sonos write'
            assert_snapshot(rpc(path, 'status'), Mode.PAUSED)
        # The client has disconnected, but the already started write may finish.
        release.set()
        assert rpc(path, 'help')['ok'] is True
        mode = Mode.PLAYING if applied else Mode.PAUSED
        assert_snapshot(wait_for_mode(path, mode), mode)
        speaker.play.assert_called_once_with()
        assert server.alive() and server.starts == [1] and speaker.stops == 0
    finally:
        release.set()
