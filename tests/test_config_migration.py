"""Offline tests for no-overwrite TOML migration."""
import tempfile
import unittest
from pathlib import Path
import importlib.util

MODULE_PATH = Path(__file__).resolve().parents[1] / 'tools/migrate_ui_config.py'
spec = importlib.util.spec_from_file_location('config_migration', MODULE_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
install_configs = migration.install_configs
PAIRS = migration.PAIRS


class ConfigMigrationTests(unittest.TestCase):
    def test_dry_run_never_creates_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            destination=Path(temp)/'missing'
            outcomes=install_configs(destination)
            self.assertEqual(len(outcomes),3)
            self.assertFalse(destination.exists())

    def test_create_configs_keep_exact_legacy_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            destination=Path(temp)/'config'
            result=install_configs(destination,dry_run=False)
            self.assertEqual(len(result),3)
            for name,legacy,example in PAIRS:
                src=legacy if legacy is not None and legacy.exists() else example
                self.assertEqual((destination/name).read_bytes(),src.read_bytes())
                self.assertEqual((destination/name).stat().st_mode & 0o777,0o600)

    def test_second_run_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            destination=Path(temp)/'config'
            install_configs(destination,dry_run=False)
            (destination/'player.toml').write_text('custom=1')
            outcomes=install_configs(destination,dry_run=False)
            self.assertEqual((destination/'player.toml').read_text(),'custom=1')
            self.assertTrue(all(result=='kept-existing' for _,result in outcomes))

    def test_existing_symlink_is_not_followed_or_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            dest=directory/'conf'
            dest.mkdir()
            other=directory/'other'
            other.write_text('sentinel')
            (dest/'player.toml').symlink_to(other)
            install_configs(dest,dry_run=False)
            self.assertEqual(other.read_text(),'sentinel')
            self.assertTrue((dest/'player.toml').is_symlink())


if __name__ == '__main__':
    unittest.main()
