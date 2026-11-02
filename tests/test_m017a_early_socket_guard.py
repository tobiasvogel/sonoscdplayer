"""Early duplicate-start refusal must not touch CD hardware."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from player_service import refuse_existing_control_socket, run


class EarlySocketGuardTests(unittest.TestCase):
    def test_existing_socket_is_rejected_before_drive_access(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            config.write_text('', encoding='utf-8')
            occupied = Path(directory) / 'control.sock'
            occupied.touch()
            with patch('player_service.detect_drive', side_effect=AssertionError('drive touched')), \\
                 patch('player_service.read_toc', side_effect=AssertionError('CD read')):
                self.assertEqual(run(['--config', str(config), '--socket', str(occupied)]), 1)
            self.assertTrue(occupied.exists())

    def test_symlink_is_also_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            dangling = Path(directory) / 'control.sock'
            dangling.symlink_to(Path(directory) / 'missing')
            with self.assertRaises(FileExistsError):
                refuse_existing_control_socket(dangling)
            self.assertTrue(dangling.is_symlink())

    def test_free_socket_path_passes_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            refuse_existing_control_socket(Path(directory) / 'unused.sock')


if __name__ == '__main__':
    unittest.main()
