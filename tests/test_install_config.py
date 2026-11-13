"""Offline tests for creating configuration files at the standard location."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_config', ROOT / 'tools/install_config.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_dry_run_never_creates_destination(self):
        destination = self.root / 'etc'
        outcomes = installer.install_configs(destination)
        self.assertEqual([o for _, o in outcomes], ['would-copy-example'] * 3)
        self.assertFalse(destination.exists())

    def test_examples_copied_byte_exact_with_restrictive_modes(self):
        destination = self.root / 'etc'
        installer.install_configs(destination, dry_run=False)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o750)
        for name in installer.NAMES:
            target = destination / name
            self.assertEqual(target.read_bytes(), installer.example_for(name).read_bytes())
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_migrates_existing_files_and_falls_back_to_examples(self):
        legacy = self.root / 'old'
        legacy.mkdir()
        (legacy / 'player.toml').write_text('[streaming]\nmax_buffer_mib = 8\n')
        destination = self.root / 'etc'
        outcomes = dict(installer.install_configs(destination, from_dir=legacy, dry_run=False))
        self.assertEqual(outcomes['player.toml'], 'copied-migrated')
        self.assertEqual(outcomes['interface.toml'], 'copied-example')
        self.assertEqual((destination / 'player.toml').read_text(),
                         '[streaming]\nmax_buffer_mib = 8\n')
        self.assertTrue((legacy / 'player.toml').exists(), 'source must not be removed')

    def test_invalid_legacy_toml_is_refused_before_writing(self):
        legacy = self.root / 'old'
        legacy.mkdir()
        (legacy / 'interface.toml').write_text('[broken\n')
        destination = self.root / 'etc'
        with self.assertRaises(Exception):
            installer.install_configs(destination, from_dir=legacy, dry_run=False)
        self.assertFalse(destination.exists())

    def test_second_run_never_overwrites(self):
        destination = self.root / 'etc'
        installer.install_configs(destination, dry_run=False)
        (destination / 'player.toml').write_text('custom = 1\n')
        outcomes = installer.install_configs(destination, dry_run=False)
        self.assertEqual((destination / 'player.toml').read_text(), 'custom = 1\n')
        self.assertTrue(all(o == 'kept-existing' for _, o in outcomes))

    def test_existing_symlink_is_not_followed_or_replaced(self):
        destination = self.root / 'etc'
        destination.mkdir()
        other = self.root / 'other'
        other.write_text('sentinel')
        (destination / 'player.toml').symlink_to(other)
        installer.install_configs(destination, dry_run=False)
        self.assertEqual(other.read_text(), 'sentinel')
        self.assertTrue((destination / 'player.toml').is_symlink())

    def test_main_uses_standard_location_override(self):
        destination = self.root / 'std'
        old = os.environ.get('SONOSCDPLAYER_CONFIG_DIR')
        os.environ['SONOSCDPLAYER_CONFIG_DIR'] = str(destination)
        try:
            self.assertEqual(installer.main(['--apply'] if os.geteuid() != 0 else
                                            ['--apply', '--service-user', 'root']), 0)
        finally:
            if old is None:
                os.environ.pop('SONOSCDPLAYER_CONFIG_DIR')
            else:
                os.environ['SONOSCDPLAYER_CONFIG_DIR'] = old
        self.assertEqual(sorted(p.name for p in destination.iterdir()),
                         sorted(installer.NAMES))


if __name__ == '__main__':
    unittest.main()
