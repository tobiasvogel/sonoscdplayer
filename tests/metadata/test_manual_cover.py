"""WEB-03c: no drive/network access, per-disc identity and image checks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
Image = pytest.importorskip('PIL.Image')

from sonoscdplayer.metadata.cover_store import CoverBudgetFull
from sonoscdplayer.metadata.manual_cover import (
    ManualCoverStore, ManualCoverWorkflow, MAX_UPLOAD_BYTES,
)
from sonoscdplayer.metadata.manual_edits import disc_token


def identity(toc='1 2 3'):
    return SimpleNamespace(disc_id='example-disc', toc=toc)


def image_bytes(kind='PNG', size=(600, 450)):
    data = BytesIO()
    Image.new('RGB', size, 'orange').save(data, format=kind)
    return data.getvalue()


def staged(store, image, name='a' * 32):
    store.staging_dir.mkdir(parents=True, exist_ok=True)
    (store.staging_dir / (name + '.upload')).write_bytes(image)
    return name


def test_manual_cover_is_resized_and_per_disc(tmp_path):
    store = ManualCoverStore(tmp_path / 'covers', tmp_path / 'staged', tmp_path / 'missing-ui.toml')
    a, b = identity('disc-one'), identity('disc-two')
    upload = staged(store, image_bytes())
    saved = store.save(a, upload, current=lambda: True)
    assert Path(saved) == store.find(a)
    assert store.find(b) is None
    assert not (store.staging_dir / (upload + '.upload')).exists()
    assert Path(saved).stat().st_mode & 0o777 == 0o600
    with Image.open(saved) as image:
        assert image.format == 'JPEG'
        assert max(image.size) <= store.preferences.max_dimension_px
    old_bytes = Path(saved).read_bytes()
    store.save(a, staged(store, image_bytes('JPEG', (220, 230))), current=lambda: True)
    assert store.find(a) is not None
    assert Path(saved).read_bytes() != old_bytes
    store.remove(a, current=lambda: True)
    assert store.find(a) is None


def test_rejects_invalid_images_and_stale_disc(tmp_path):
    store = ManualCoverStore(tmp_path / 'covers', tmp_path / 'staged', tmp_path / 'missing-ui.toml')
    a = identity()
    for data in (b'not an image', b'PK' + b'0' * 200):
        upload = staged(store, data)
        with pytest.raises(ValueError):
            store.save(a, upload, current=lambda: True)
        assert store.find(a) is None
    upload = staged(store, image_bytes())
    with pytest.raises(ValueError, match='disc changed'):
        store.save(a, upload, current=lambda: False)
    assert store.find(a) is None
    with pytest.raises(ValueError):
        store.save(a, '../unsafe', current=lambda: True)
    with pytest.raises(ValueError, match='disc changed'):
        store.remove(a, current=lambda: False)


def test_cover_budget_includes_other_covers_and_replacements(tmp_path):
    store = ManualCoverStore(tmp_path / 'covers', tmp_path / 'staged', tmp_path / 'missing-ui.toml')
    store.cover_dir.mkdir()
    store.preferences = SimpleNamespace(max_dimension_px=512, jpeg_quality=85, budget_mb=1)
    # A full cache must never be automatically cleaned to make room.
    (store.cover_dir / 'cached.jpg').write_bytes(b'x' * (1024 * 1024 - 10))
    with pytest.raises(CoverBudgetFull):
        store.save(identity(), staged(store, image_bytes()), current=lambda: True)
    assert (store.cover_dir / 'cached.jpg').exists()


def test_worker_rejects_stale_token_and_ignores_old_completion(tmp_path):
    a, b = identity('toc-a'), identity('toc-b')
    class Dummy(ManualCoverWorkflow):
        def __init__(self, executor):
            self._executor = executor
            self._identify = lambda toc: toc
            self._last_toc = a
            self._result = {'edit_token': disc_token(a), 'cover_file': None}
            self._init_manual_covers(tmp_path / 'covers', tmp_path / 'missing-ui.toml', tmp_path / 'staged')
    with ThreadPoolExecutor(max_workers=1) as executor:
        w = Dummy(executor)
        with pytest.raises(ValueError, match='disc changed'):
            w.enqueue_cover(token=disc_token(b), remove=True)
        unblock = Event()
        executor.submit(lambda: unblock.wait(5))
        w.enqueue_cover(token=disc_token(a), upload_id=staged(w._manual_covers, image_bytes()))
        w._last_toc = b
        w._reset_manual_covers()
        w._result = {'edit_token': disc_token(b), 'cover_file': None}
        unblock.set()
        with pytest.raises(ValueError, match='disc changed'):
            w._cover_pending[1].result(timeout=5)
        assert not w._poll_manual_covers()
        assert w._cover_path is None
        assert w._manual_covers.find(a) is None
