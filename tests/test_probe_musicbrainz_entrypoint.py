"""Regression: the probe CLI entry point must be executable without a drive."""
import contextlib
import io
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch


class ProbeMusicBrainzEntrypointTests(unittest.TestCase):
    def test_main_is_exported(self):
        path = Path(__file__).resolve().parents[1] / 'tools' / 'probe_musicbrainz.py'
        namespace = runpy.run_path(str(path), run_name='sonoscdplayer_probe_test')
        self.assertTrue(callable(namespace.get('main')))
        self.assertEqual(namespace['main'].__name__, 'main')

    def test_script_help_exits_cleanly(self):
        path = Path(__file__).resolve().parents[1] / 'tools' / 'probe_musicbrainz.py'
        output = io.StringIO()
        with patch.object(sys, 'argv', [str(path), '--help']):
            with contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(str(path), run_name='__main__')
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('--cache-db', output.getvalue())
        self.assertIn('--cover-dir', output.getvalue())


if __name__ == '__main__':
    unittest.main()
