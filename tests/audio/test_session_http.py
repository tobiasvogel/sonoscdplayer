"""Session-scoped HTTP resources, using real HTTP and synthetic PCM."""
from contextlib import contextmanager
import http.client
import sys
import threading
import time
from unittest.mock import Mock

import pytest

from sonoscdplayer.audio.track_http_server import (
    CHUNK, DiscServer, TrackServer, TrackStream, wav_header,
)

SESSION = '0123456789abcdef0123456789abcdef'
OLD = 'fedcba9876543210fedcba9876543210'
SIZES = {1: CHUNK, 2: 2 * CHUNK}


def path(track, session=SESSION):
    return f'/session/{session}/track/{track}.wav'


def pcm(track):
    return bytes([64 + track]) * SIZES[track]


def command(track):
    return [sys.executable, '-c',
            f'import sys; sys.stdout.buffer.write(bytes([{64 + track}])*{SIZES[track]})']


@contextmanager
def running_server(kind, *, session=SESSION, start=False, cover=None):
    stream = TrackStream(SIZES[1], 4 * CHUNK, command(1), write_timeout=3)
    options = {'cover': cover}
    if session is not None:
        options['session_id'] = session
    if kind == 'disc':
        server = DiscServer(('127.0.0.1', 0), stream, 1, tracks=SIZES,
                            command_for=command, **options)
    else:
        server = TrackServer(('127.0.0.1', 0), stream, 1, **options)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    try:
        if start:
            stream.start()
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def request(server, method, route, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=8)
    try:
        conn.request(method, route, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


@pytest.mark.parametrize('kind', ['track', 'disc'])
def test_current_session_head_does_not_attach_reader(kind):
    with running_server(kind) as server:
        before = server.stream.ring.stats.copy()
        acquire = Mock(side_effect=AssertionError('HEAD attached a reader'))
        server.acquire_get = acquire
        status, headers, body = request(server, 'HEAD', path(1))
        assert status == 200
        assert headers['Content-Length'] == str(SIZES[1] + 44)
        assert body == b''
        assert server.stream.ring.stats == before
        acquire.assert_not_called()


@pytest.mark.parametrize('kind', ['track', 'disc'])
@pytest.mark.parametrize('route', [path(1, OLD), path(2, OLD), '/track/1.wav',
                                  path(1, 'invalid'), path(99)])
@pytest.mark.parametrize('method, headers', [('HEAD', {}), ('GET', {}),
                                             ('GET', {'Range': 'bytes=44-47'})])
def test_invalid_resource_is_rejected_without_reader_actions(kind, route, method, headers):
    with running_server(kind) as server:
        stream = server.stream
        before = stream.ring.stats.copy()
        generation = server._generation
        anchor = stream.anchor
        acquire = Mock(side_effect=AssertionError('invalid resource attached a reader'))
        probe = Mock(side_effect=AssertionError('invalid resource accessed probe cache'))
        server.acquire_get = acquire
        server.try_cached_probe = probe
        status, _, body = request(server, method, route, headers)
        assert (status, body) == (404, b'')
        acquire.assert_not_called()
        probe.assert_not_called()
        assert server.stream is stream
        assert server.track == 1
        assert server._generation == generation
        assert stream.anchor == anchor
        assert stream.ring.stats == before
        assert stream.thread.ident is None


@pytest.mark.parametrize('kind', ['track', 'disc'])
def test_current_session_full_get(kind):
    with running_server(kind, start=True) as server:
        status, headers, body = request(server, 'GET', path(1))
        assert status == 200
        assert headers['Content-Length'] == str(SIZES[1] + 44)
        assert body == wav_header(SIZES[1]) + pcm(1)


@pytest.mark.parametrize('kind', ['track', 'disc'])
def test_current_session_range_get(kind):
    with running_server(kind, start=True) as server:
        status, headers, body = request(server, 'GET', path(1),
                                        {'Range': 'bytes=40-47'})
        assert status == 206
        assert headers['Content-Range'] == f'bytes 40-47/{SIZES[1] + 44}'
        assert headers['Content-Length'] == '8'
        assert body == (wav_header(SIZES[1]) + pcm(1))[40:48]


@pytest.mark.parametrize('kind', ['track', 'disc'])
def test_current_session_invalid_range(kind):
    with running_server(kind) as server:
        status, headers, body = request(server, 'GET', path(1),
                                        {'Range': f'bytes={SIZES[1] + 44}-'})
        assert (status, body) == (416, b'')
        assert headers['Content-Range'] == f'bytes */{SIZES[1] + 44}'


def test_disc_prefetch_preserves_session_route():
    with running_server('disc', start=True) as server:
        assert request(server, 'GET', path(1))[2] == wav_header(SIZES[1]) + pcm(1)
        deadline = time.monotonic() + 5
        while server.track != 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.track == 2
        assert server.route == path(2)
        assert request(server, 'HEAD', path(2, OLD))[0] == 404
        status, _, body = request(server, 'GET', path(2))
        assert (status, body) == (200, wav_header(SIZES[2]) + pcm(2))


@pytest.mark.parametrize('kind', ['track', 'disc'])
def test_cover_route_unchanged(kind, tmp_path):
    cover = tmp_path / ('a' * 64 + '.jpg')
    cover.write_bytes(b'\xff\xd8\xff\xd9')
    with running_server(kind, cover=str(cover)) as server:
        status, _, body = request(server, 'GET', '/cover/' + cover.name)
        assert (status, body) == (200, cover.read_bytes())


@pytest.mark.parametrize('kind', ['track', 'disc'])
def test_standalone_legacy_route(kind):
    with running_server(kind, session=None) as server:
        assert request(server, 'HEAD', '/track/1.wav')[0] == 200
        assert request(server, 'HEAD', path(1))[0] == 404
