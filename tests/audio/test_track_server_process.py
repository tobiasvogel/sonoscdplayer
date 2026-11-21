"""Stream URLs and waiting for the track server's CD reader."""
import queue
from types import SimpleNamespace

import pytest

from sonoscdplayer.audio.track_server_process import track_stream_url, wait_for_reader_start

LISTENING = 'Serving http://0.0.0.0:8765/track/1.wav'


def server_output(*lines, exit_code=None):
    """A queue with the given output lines and a matching fake process."""
    output = queue.Queue()
    for line in lines:
        output.put((0, line))
    return output, SimpleNamespace(poll=lambda: exit_code, returncode=exit_code)


def test_url_names_host_port_and_track():
    assert track_stream_url('192.0.2.12', 8765, 2) == 'http://192.0.2.12:8765/track/2.wav'


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1', '0.0.0.0', '224.0.0.1',
                                  '169.254.1.1', '192.0.2.3/x', 'bad'])
def test_url_needs_a_lan_ipv4_address(host):
    with pytest.raises(ValueError):
        track_stream_url(host, 8765, 2)


@pytest.mark.parametrize('port, track', [(0, 2), (65536, 2), (8765, 0)])
def test_url_needs_a_valid_port_and_track(port, track):
    with pytest.raises(ValueError):
        track_stream_url('192.0.2.12', port, track)


def test_waiting_ends_when_the_reader_starts():
    wait_for_reader_start(*server_output(LISTENING, '[+0.01s] READER started'), timeout=0.1)


@pytest.mark.parametrize('lines, message', [
    ((LISTENING, '[+0.01s] READER end written=0 error=X'), 'already finished'),
    (('[+0.01s] READER started',), 'before the server was listening'),
    ((LISTENING, None), 'closed its output'),
])
def test_unexpected_server_output_is_an_error(lines, message):
    with pytest.raises(RuntimeError, match=message):
        wait_for_reader_start(*server_output(*lines), timeout=0.1)


def test_an_exited_server_is_an_error():
    with pytest.raises(RuntimeError, match='exited'):
        wait_for_reader_start(*server_output(exit_code=1), timeout=0.1)


def test_silence_times_out():
    with pytest.raises(TimeoutError):
        wait_for_reader_start(*server_output(LISTENING), timeout=0.05)


def test_output_after_the_start_is_logged_but_not_queued(capsys):
    import queue
    import threading
    from types import SimpleNamespace
    from sonoscdplayer.audio.track_server_process import forward_server_output
    lines = queue.Queue()
    done = threading.Event()
    done.set()                                   # startup already finished
    process = SimpleNamespace(stdout=iter(f'DIAG {i}\n' for i in range(10000)))
    forward_server_output(process, lines, 0.0, startup_done=done)
    assert lines.qsize() == 1                    # only the end marker
    assert 'DIAG 9999' in capsys.readouterr().out
