"""AUD-04i: failures before the worker loop must not strand a socket."""
import socket
from unittest.mock import Mock, patch

import pytest

from sonoscdplayer.player.service import serve


def test_listen_failure_releases_socket_and_path(tmp_path):
    endpoint = tmp_path / 'control.sock'
    actual_socket = socket.socket
    created = []

    def faulty_socket(*args, **kwargs):
        raw = actual_socket(*args, **kwargs)
        created.append(raw)
        wrapper = Mock(wraps=raw)
        wrapper.listen.side_effect = OSError('listen failed')
        return wrapper

    with patch('sonoscdplayer.player.service.socket.socket', side_effect=faulty_socket):
        with pytest.raises(OSError, match='listen failed'):
            serve(Mock(), str(endpoint))

    assert created and created[0].fileno() == -1
    assert not endpoint.exists()
    with actual_socket(socket.AF_UNIX, socket.SOCK_STREAM) as next_server:
        next_server.bind(str(endpoint))
