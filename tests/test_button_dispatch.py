"""Command dispatch contract tests; all network calls are mocked."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.interfaces.interface_input import handle_event, read_uid


class DispatchTests(unittest.TestCase):
    @patch('integrations.interfaces.interface_input.send_command')
    def test_passive_never_sends(self, send):
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('stop', 'pressed')
            handle_event('playpause', 'pressed', commands=True)
        send.assert_not_called()

    @patch('integrations.interfaces.interface_input.send_command')
    def test_execute_only_on_supported_press(self, send):
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('skip', 'pressed', commands=True, execute=True, uid='RINCON_TEST')
            handle_event('skip', 'released', commands=True, execute=True, uid='RINCON_TEST')
            handle_event('encoder', 'right', commands=True, execute=True, uid='RINCON_TEST')
        send.assert_called_once_with('/run/sonoscdplayer/control.sock', 'next', 'RINCON_TEST')

    @patch('integrations.interfaces.interface_input.send_command')
    def test_playpause_without_uid_skipped(self, send):
        with contextlib.redirect_stdout(io.StringIO()):
            handle_event('playpause', 'pressed', commands=True, execute=True)
        send.assert_not_called()

    def test_uid_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'uid'
            path.write_text('RINCON_TEST\n')
            path.chmod(0o600)
            self.assertEqual(read_uid(path), 'RINCON_TEST')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                read_uid(path)


if __name__ == '__main__':
    unittest.main()
