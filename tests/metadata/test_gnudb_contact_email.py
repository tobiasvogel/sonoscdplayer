# M4c.5a: contact email config and provider requests without real network.
import importlib.util
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from sonoscdplayer.metadata.gnudb import (hello_for_contact, valid_contact_email,
                                           identity_from_toc, query_disc, CDDBMatch, read_entry)
from sonoscdplayer.audio.table_of_contents import DiscTOC, Track
from sonoscdplayer.metadata.worker import (gnudb_settings_from_config,
    gnudb_contact_from_config, MetadataWorker)


class Response:
    def __init__(self, data):
        self.data = data
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, _): return self.data


class ContactTests(unittest.TestCase):
    def test_missing_contact_keeps_legacy_config(self):
        self.assertEqual(gnudb_contact_from_config({}), '')
        self.assertIn('anonymous', hello_for_contact(''))
        self.assertEqual(len(gnudb_settings_from_config({})), 3)

    def test_config_accepts_contact(self):
        data = {'gnudb': {'contact_email': 'tester@example.org'}}
        self.assertEqual(gnudb_contact_from_config(data), 'tester@example.org')

    def test_rejects_bad_contact(self):
        for address in ('a b@example.org', 'x@example.org\\nY', 'invalid',
                        'a@localhost', '@example.org', 'ä@example.org'):
            with self.subTest(address=address):
                self.assertFalse(valid_contact_email(address))
                with self.assertRaises(ValueError):
                    gnudb_contact_from_config({'gnudb': {'contact_email': address}})

    def test_query_uses_contact_in_hello_without_logging(self):
        toc = DiscTOC((Track(1, 0, 7500, 2, False),), 7500)
        identity = identity_from_toc(toc)
        seen = []
        def opener(req, timeout):
            seen.append(req.full_url)
            return Response(b'202 No match\\n')
        result = query_disc(identity, opener=opener, contact_email='tester@example.org')
        self.assertEqual(result.status, 'not_found')
        self.assertEqual(parse_qs(urlsplit(seen[0]).query)['hello'][0],
                         'tester example.org SonosCDPlayer 0.9')

    def test_worker_validates_contact_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = MetadataWorker(cache_path=Path(tmp)/'metadata.sqlite3',
                cover_dir=Path(tmp)/'covers', online=False,
                gnudb_contact_email='tester@example.org')
            try:
                self.assertEqual(worker._gnudb_contact_email, 'tester@example.org')
            finally:
                worker.close()
            with self.assertRaises(ValueError):
                MetadataWorker(cache_path=Path(tmp)/'bad.db',
                    cover_dir=Path(tmp)/'bad_covers', online=False,
                    gnudb_contact_email='invalid')


if __name__ == '__main__':
    unittest.main()
