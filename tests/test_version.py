"""OPS-02: one version number, visible in the player status."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import version

ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_version_file_is_valid_and_in_the_changelog(self):
        number = version.release()
        self.assertRegex(number, r'^\d+\.\d+\.\d+')
        self.assertIn(f'## {number}', (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'))

    def test_unreadable_or_bad_version_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(version.release(Path(tmp)), 'unknown')
            (Path(tmp) / 'VERSION').write_text('next\n')
            self.assertEqual(version.release(Path(tmp)), 'unknown')
            self.assertIsNone(version.commit(Path(tmp)))      # not a git checkout

    def test_command_line(self):
        out = subprocess.run([sys.executable, str(ROOT / 'src/version.py')],
                             capture_output=True, text=True, check=True).stdout
        self.assertTrue(out.startswith('SonosCDPlayer ' + version.release()))


if __name__ == '__main__':
    unittest.main()
