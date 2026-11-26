"""Resource identity of session-scoped audio URLs."""
import pytest

from sonoscdplayer.audio.track_server_process import track_stream_url

HOST = '192.0.2.12'
SESSION_A = '0123456789abcdef0123456789abcdef'
SESSION_B = 'fedcba9876543210fedcba9876543210'


def test_session_url_names_host_port_session_and_track():
    assert track_stream_url(HOST, 8765, 2, session_id=SESSION_A) == (
        f'http://{HOST}:8765/session/{SESSION_A}/track/2.wav'
    )


def test_same_track_in_different_sessions_has_distinct_urls():
    assert track_stream_url(HOST, 8765, 1, session_id=SESSION_A) != (
        track_stream_url(HOST, 8765, 1, session_id=SESSION_B)
    )


def test_same_session_and_track_has_stable_url():
    first = track_stream_url(HOST, 8765, 1, session_id=SESSION_A)
    assert track_stream_url(HOST, 8765, 1, session_id=SESSION_A) == first
    assert track_stream_url(HOST, 8765, 2, session_id=SESSION_A) != first


def test_omitted_session_preserves_standalone_url():
    assert track_stream_url(HOST, 8765, 1) == f'http://{HOST}:8765/track/1.wav'


@pytest.mark.parametrize('session_id', [
    '', 'a' * 31, 'a' * 33, 'g' * 32, 'A' * 32,
    SESSION_A + '/', SESSION_A + '?x=1', SESSION_A + '#fragment',
    SESSION_A + '\n', '../' + SESSION_A, ' ' + SESSION_A,
    123, True, SESSION_A.encode('ascii'),
])
def test_invalid_session_id_is_rejected(session_id):
    with pytest.raises(ValueError):
        track_stream_url(HOST, 8765, 1, session_id=session_id)


@pytest.mark.parametrize('host, port, track', [
    ('localhost', 8765, 1), ('127.0.0.1', 8765, 1),
    ('0.0.0.0', 8765, 1), ('224.0.0.1', 8765, 1),
    ('169.254.1.1', 8765, 1), ('192.0.2.3/x', 8765, 1),
    (HOST, 0, 1), (HOST, 65536, 1), (HOST, 8765, 0),
])
def test_session_url_preserves_existing_validation(host, port, track):
    with pytest.raises(ValueError):
        track_stream_url(host, port, track, session_id=SESSION_A)
