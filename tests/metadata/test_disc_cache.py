"""Temporary SQLite and Cover cache, no network."""
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sonoscdplayer.metadata.cache import DiscCache
from sonoscdplayer.metadata.cover_store import CoverStore, CoverPolicy
from sonoscdplayer.metadata.local_metadata import LocalDiscMetadata, LocalTrackMetadata
from sonoscdplayer.metadata.musicbrainz import DiscIdentity

RELEASE = 'f9cb3073-ac5d-4747-80e9-b69bcba0f366'


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'meta.sqlite3'
        self.cache = DiscCache(self.db)
        self.disc = DiscIdentity('A' * 28, '1 1 375 150')
        self.local = LocalDiscMetadata('Música', 'Artiste',
            (LocalTrackMetadata(1, 180, 'Canción', 'Artiste', 'CHA141502016'),),
            'available')

    def test_read_without_write(self):
        self.assertIsNone(self.cache.get(self.disc))
        self.assertFalse(self.db.exists())

    def test_sqlite_roundtrip(self):
        self.assertTrue(self.cache.put(self.disc, self.local,
            release_id=RELEASE, match_method='confidence', confidence_score=96))
        got = self.cache.get(self.disc)
        self.assertEqual(got.local, self.local)
        self.assertEqual(got.release_id, RELEASE)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_disc_id_collision_isolated_by_toc(self):
        self.cache.put(self.disc, self.local)
        other = DiscIdentity(self.disc.disc_id, '1 1 376 150')
        self.assertIsNone(self.cache.get(other))

    def test_manual_confirmation_is_protected(self):
        self.cache.put(self.disc, self.local, release_id=RELEASE,
                       match_method='manual', confirmed_by_user=True)
        self.assertFalse(self.cache.put(self.disc, self.local, release_id='another',
                         match_method='confidence', confidence_score=99))
        self.assertTrue(self.cache.get(self.disc).confirmed_by_user)

    def test_invalid_score(self):
        with self.assertRaises(ValueError):
            self.cache.put(self.disc, self.local, release_id=RELEASE,
                           match_method='confidence', confidence_score=101)


class CoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = CoverStore(Path(self.tmp.name) / 'covers')

    def test_disabled_no_files_created(self):
        self.assertIsNone(self.cache.get_or_download(RELEASE))
        self.assertFalse(self.cache.directory.exists())

    def test_release_id_is_not_path(self):
        with self.assertRaises(ValueError):
            self.cache.find('../path')

    def test_invalid_cover_settings(self):
        for size in (0, 63, 2049, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                CoverPolicy(enabled=True, max_dimension_px=size)

    def test_resize_without_upscale(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow is an optional cover-only dependency')
        class Response:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None
            def read(self, count):
                return self.payload[:count]
        def payload(dimensions):
            file = BytesIO()
            Image.new('RGB', dimensions, 'white').save(file, 'PNG')
            return file.getvalue()
        for size, expected in [((120, 80), (120, 80)),
                               ((1000, 700), (300, 210))]:
            with self.subTest(size=size):
                self.cache._path(RELEASE).unlink(missing_ok=True)
                result = self.cache.get_or_download(
                    RELEASE, policy=CoverPolicy(enabled=True, max_dimension_px=300),
                    opener=lambda request, timeout: Response(payload(size)))
                self.assertIsNotNone(result)
                with Image.open(result) as image:
                    self.assertEqual(image.size, expected)

    def test_one_file_per_release(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow not available')
        class Response:
            def __enter__(self):
                b = BytesIO()
                Image.new('RGB', (100, 100), 'white').save(b, 'PNG')
                self.data = b.getvalue()
                return self
            def __exit__(self, *_):
                pass
            def read(self, count):
                return self.data[:count]
        policy = CoverPolicy(enabled=True, max_dimension_px=256)
        first = self.cache.get_or_download(RELEASE, policy=policy,
                                            opener=lambda req, timeout: Response())
        second = self.cache.get_or_download(RELEASE, policy=policy,
                                             opener=lambda *_: self.fail('download repeated'))
        self.assertEqual(first, second)
        self.assertEqual(len(list(self.cache.directory.glob('*.jpg'))), 1)


if __name__ == '__main__':
    unittest.main()
