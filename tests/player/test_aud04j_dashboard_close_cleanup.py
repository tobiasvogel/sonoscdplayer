"""AUD-04j: dashboard cleanup errors cannot strand the player socket."""
import threading
from unittest.mock import Mock, patch

import pytest

from sonoscdplayer.player.service import serve


def test_dashboard_close_failure_still_releases_socket(tmp_path):
    path = tmp_path / 'control.sock'
    dashboard = Mock()
    dashboard.close.side_effect = RuntimeError('dashboard close failed')
    stopped = threading.Event()
    stopped.set()
    with (patch('sonoscdplayer.player.service.DashboardPublisher', return_value=dashboard),
          patch('sonoscdplayer.player.service.SelectedTarget') as selected,
          patch('sonoscdplayer.player.service.StatusPublisher')):
        selected.return_value.restore.return_value = False
        with pytest.raises(RuntimeError, match='dashboard close failed'):
            serve(Mock(), str(path), stop_event=stopped)
    dashboard.close.assert_called_once_with()
    assert not path.exists()
