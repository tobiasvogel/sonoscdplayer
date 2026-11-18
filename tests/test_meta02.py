"""META-02 (18.11.): cover budget and quality, resetting the inserted CD's entry."""
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from disc_cache import DiscCache
from disc_covers import CoverBudgetFull, CoverCache, CoverPolicy
from disc_metadata import LocalDiscMetadata, LocalTrackMetadata
from disc_musicbrainz import DiscIdentity, LookupResult, ReleaseCandidate
from player_metadata import MetadataWorker
from sonos_ui.interactive_menu import InteractiveMenu
from cover_preferences import read_cover_preferences

from test_player_metadata import FakeToc, ManualExecutor

RELEASE = 'f9cb3073-ac5d-4747-80e9-b69bcba0f366'
OTHER = '0f9cb307-3ac5-4d47-80e9-b69bcba0f366'


def picture_response(seed=0):
    from PIL import Image
    import random
    rng = random.Random(seed)

    class Response:
        def __enter__(self):
            image = Image.new('RGB', (200, 200))
            image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                           for _ in range(200 * 200)])
            buffer = BytesIO()
            image.save(buffer, 'PNG')
            self.data = buffer.getvalue()
            return self

        def __exit__(self, *_):
            pass

        def read(self, count):
            return self.data[:count]
    return Response()


class CoverTests(unittest.TestCase):
    def setUp(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest('Pillow not available')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = CoverCache(Path(self.tmp.name) / 'covers')

    def test_full_budget_keeps_old_and_stores_no_new_cover(self):
        policy = CoverPolicy(enabled=True, budget_mb=1)
        first = self.cache.get_or_download(RELEASE, policy=policy,
                                           opener=lambda r, timeout: picture_response(1))
        self.assertIsNotNone(first)
        filler = self.cache.directory / ('f' * 64 + '.jpg')
        filler.write_bytes(b'x' * (1024 * 1024))           # budget now exceeded
        with self.assertRaises(CoverBudgetFull):
            self.cache.get_or_download(OTHER, policy=policy,
                                       opener=lambda r, timeout: picture_response(2))
        self.assertTrue(first.exists())
        self.assertEqual(len(list(self.cache.directory.glob('*.jpg'))), 2)

    def test_quality_changes_the_stored_file(self):
        low = self.cache.get_or_download(RELEASE, policy=CoverPolicy(enabled=True, jpeg_quality=40),
                                         opener=lambda r, timeout: picture_response(3))
        size_low = low.stat().st_size
        low.unlink()
        high = self.cache.get_or_download(RELEASE, policy=CoverPolicy(enabled=True, jpeg_quality=95),
                                          opener=lambda r, timeout: picture_response(3))
        self.assertGreater(high.stat().st_size, size_low)

    def test_settings_in_ui_toml(self):
        path = Path(self.tmp.name) / 'ui.toml'
        path.write_text('[ui.cover]\nmode = "on"\njpeg_quality = 70\nbudget_mb = 20\n')
        policy = read_cover_preferences(path).download_policy(display_supports_cover=False)
        self.assertEqual((policy.enabled, policy.jpeg_quality, policy.budget_mb,
                          policy.max_dimension_px), (True, 70, 20, 512))
        for bad in ('jpeg_quality = 100', 'jpeg_quality = 10', 'budget_mb = 0'):
            path.write_text('[ui.cover]\n' + bad + '\n')
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                read_cover_preferences(path)


class ResetDiscTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.executor = ManualExecutor()
        self.lookup = Mock(return_value=LookupResult('unique', (
            ReleaseCandidate('release', 'An Album', 'An Artist', None, None, ('Track one',)),)))
        self.db = Path(self.tmp.name) / 'metadata.sqlite3'
        self.worker = MetadataWorker(cache_path=self.db, cover_dir=Path(self.tmp.name) / 'covers',
                                     executor=self.executor,
                                     identify=lambda toc: DiscIdentity('A' * 28, toc.label),
                                     lookup=self.lookup)

    def test_reset_deletes_the_entry_and_looks_up_again(self):
        toc = FakeToc('disc')
        self.worker.observe(toc)
        self.executor.finish(0)
        self.worker.poll(toc)
        self.assertIsNotNone(DiscCache(self.db).get(DiscIdentity('A' * 28, 'disc')))
        self.assertTrue(self.worker.reset_current())
        self.assertIsNone(self.worker.snapshot)
        self.executor.finish(1)
        self.assertTrue(self.worker.poll(toc))
        self.assertEqual(self.worker.snapshot['match_status'], 'unique')   # not 'cached'
        self.assertEqual(self.lookup.call_count, 2)

    def test_reset_without_disc_is_refused(self):
        with self.assertRaises(ValueError):
            self.worker.reset_current()

    def test_cache_delete_also_removes_manual_choice(self):
        cache = DiscCache(self.db)
        disc = DiscIdentity('B' * 28, '1 1 375 150')
        local = LocalDiscMetadata('A', 'B', (LocalTrackMetadata(1, 180, 'T'),), 'available')
        cache.put(disc, local, release_id='r', match_method='manual', confirmed_by_user=True)
        self.assertTrue(cache.delete(disc))
        self.assertIsNone(cache.get(disc))
        self.assertFalse(cache.delete(disc))


class ResetMenuTests(unittest.TestCase):
    def open_reset(self, menu):
        menu.active, menu.section = True, 'settings'
        menu.menu_focus = 2                                   # "Reset CD entry"
        menu.event('encoder_sw', 'pressed', now=0)
        self.assertEqual((menu.section, menu.menu_focus), ('reset_disc', 0))

    def test_back_is_preselected_and_changes_nothing(self):
        calls = []
        menu = InteractiveMenu(discover=lambda **_: None, disc_reset=lambda: calls.append(1) or True)
        self.open_reset(menu)
        menu.event('encoder_sw', 'pressed', now=0)            # "Back"
        self.assertEqual((menu.section, menu.menu_focus), ('settings', 2))
        self.assertEqual(calls, [])

    def test_confirm_resets_and_reports(self):
        done = threading.Event()
        menu = InteractiveMenu(discover=lambda **_: None,
                               disc_reset=lambda: done.set() or True)
        self.open_reset(menu)
        menu.event('encoder', 'right', now=0)
        menu.event('encoder_sw', 'pressed', now=0)
        self.assertTrue(done.wait(2))
        for _ in range(100):
            menu.poll(now=1)
            if menu.connect_notices.current(now=1) is not None:
                break
            threading.Event().wait(0.01)
        self.assertEqual(menu.connect_notices.current(now=1).key, 'status.disc_reset_done')
        self.assertEqual(menu.section, 'settings')

    def test_failure_is_reported(self):
        menu = InteractiveMenu(discover=lambda **_: None, disc_reset=None)
        self.open_reset(menu)
        menu.event('encoder', 'right', now=0)
        menu.event('encoder_sw', 'pressed', now=0)
        self.assertEqual(menu.connect_notices.current(now=0).key, 'status.disc_reset_failed')


if __name__ == '__main__':
    unittest.main()
