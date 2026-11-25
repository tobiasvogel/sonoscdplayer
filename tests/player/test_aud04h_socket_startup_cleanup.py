"""AUD-04h: failure during socket startup must release the local endpoint."""
import socket
from unittest.mock import Mock, patch

import pytest

from sonoscdplayer.player.service import serve


def test_target_restore_failure_cleans_bound_socket(tmp_path):
    path = tmp_path / 'control.sock'
    controller = Mock()
    # Deliberately fail *after* bind/listen, but before the worker loop.
    with (patch('sonoscdplayer.player.service.StatusPublisher'),
          patch('sonoscdplayer.player.service.SelectedTarget') as selection):
        selection.return_value.restore.side_effect = ValueError('bad saved target')
        with pytest.raises(ValueError, match='bad saved target'):
            serve(controller, str(path))
    assert not path.exists()
    # The address must be immediately reusable without manual file cleanup.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as next_server:
        next_server.bind(str(path))
