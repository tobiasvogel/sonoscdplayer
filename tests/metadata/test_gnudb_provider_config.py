"""M4c.4: offline configuration, provider isolation and endpoint checks."""
import importlib.util
from pathlib import Path
import tempfile
import tomllib
import unittest

from sonoscdplayer.metadata.worker import gnudb_settings_from_config, MetadataWorker
from sonoscdplayer.metadata.gnudb import ENDPOINT

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('configure_m4c4', ROOT / 'tools/configure.py')
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)


class ProviderConfigurationTests(unittest.TestCase):
    def test_default_without_optional_section(self):
        self.assertEqual(gnudb_settings_from_config({}), (True, 5, ENDPOINT))

    def test_disabled_and_custom_https_endpoint(self):
        endpoint = 'https://example.org/cddb.cgi'
        self.assertEqual(gnudb_settings_from_config({'gnudb': {
            'enabled': False, 'timeout_seconds': 12, 'endpoint': endpoint}}),
            (False, 12, endpoint))

    def test_invalid_endpoint_and_types_fail_closed(self):
        bad = ('http://example.org/cddb.cgi', 'https://user:pass@example.org/x',
               'https://example.org/x?q=1', 'https://example.org/x#fragment',
               'https://example.org:8443/cddb.cgi', 'https://localhost/',
               'https://example.org/x\nX: bad')
        for endpoint in bad:
            # localhost is technically HTTPS and configurable, but unsafe by policy.
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                gnudb_settings_from_config({'gnudb': {'endpoint': endpoint}})
        for setting in ({'enabled': 'yes'}, {'timeout_seconds': True},
                        {'timeout_seconds': 0}, {'bogus': 'x'}):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                gnudb_settings_from_config({'gnudb': setting})

    def test_wizard_catalogue_is_exposed(self):
        group = wizard.PROVIDER_MENU_GROUP[1]
        self.assertEqual(group, (('metadata.gnudb', 'enabled'),
                                 ('metadata.gnudb', 'timeout_seconds'),
                                 ('metadata.gnudb', 'endpoint')))

    def test_wizard_plan_validates_and_selects_player_only(self):
        raw = (ROOT/'config/player.example.toml').read_text()
        current = wizard.existing_values(tomllib.loads(raw), tomllib.loads(raw))
        path, updated, _, services = wizard.plan_update(
            {('metadata.gnudb', 'timeout_seconds'): 9}, current, {},
            player_path=ROOT/'player.toml', ui_path=ROOT/'ui.toml',
            raw=raw, ui_raw=None)
        self.assertEqual(path.name, 'player.toml')
        self.assertEqual(services, ['sonoscdplayer-player.service'])
        self.assertEqual(tomllib.loads(updated)['metadata']['gnudb']['timeout_seconds'], 9)

    def test_invalid_wizard_endpoint_rejected(self):
        raw = (ROOT/'config/player.example.toml').read_text()
        current = wizard.existing_values(tomllib.loads(raw), tomllib.loads(raw))
        with self.assertRaises(ValueError):
            wizard.plan_update({('metadata.gnudb', 'endpoint'): 'http://invalid/'},
                current, {}, player_path=ROOT/'player.toml', ui_path=ROOT/'ui.toml',
                raw=raw, ui_raw=None)

    def test_worker_validates_custom_endpoint_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = MetadataWorker(cache_path=Path(temp)/'cache.db',
                cover_dir=Path(temp)/'covers', online=False,
                gnudb_enabled=False, gnudb_endpoint='https://example.org/cddb.cgi')
            try:
                self.assertEqual(worker._gnudb_endpoint, 'https://example.org/cddb.cgi')
            finally:
                worker.close()


if __name__ == '__main__':
    unittest.main()
