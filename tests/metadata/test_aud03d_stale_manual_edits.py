"""AUD-03d: a queued manual edit must not persist across CD sessions."""

import pytest

from sonoscdplayer.metadata.manual_edits import ManualEdits
from sonoscdplayer.metadata.musicbrainz import DiscIdentity, LookupResult
from sonoscdplayer.metadata.worker import MetadataWorker
from test_metadata_worker import FakeToc, ManualExecutor


def test_stale_edit_after_cd_change(tmp_path):
    executor = ManualExecutor()
    db = tmp_path / 'metadata.sqlite3'
    worker = MetadataWorker(cache_path=db, cover_dir=tmp_path / 'covers',
                            executor=executor, online=False,
                            identify=lambda toc: DiscIdentity('A' * 28, toc.label))
    first = FakeToc('disc-a')
    worker.observe(first)
    executor.finish(0)
    worker.poll(first)
    worker.enqueue_edit(token=worker.snapshot['edit_token'], field='album', value='stale')
    assert len(executor.jobs) == 2
    worker.observe(FakeToc('disc-b'))
    # ManualExecutor exposes the queued call so we can verify cancellation.
    future, work, args = executor.jobs[1]
    with pytest.raises(ValueError, match='disc changed'):
        work(*args)
    assert ManualEdits(db).get(DiscIdentity('A' * 28, 'disc-a')) == {}


def test_stale_edit_does_not_touch_reinserted_cd(tmp_path):
    executor = ManualExecutor()
    db = tmp_path / 'metadata.sqlite3'
    worker = MetadataWorker(cache_path=db, cover_dir=tmp_path / 'covers',
                            executor=executor, online=False,
                            identify=lambda toc: DiscIdentity('A' * 28, toc.label))
    first = FakeToc('disc-a')
    worker.observe(first)
    executor.finish(0)
    worker.poll(first)
    token = worker.snapshot['edit_token']
    worker.enqueue_edit(token=token, field='album', value='stale')
    worker.observe(None)
    worker.observe(FakeToc('disc-a'))
    future, work, args = executor.jobs[1]
    with pytest.raises(ValueError, match='disc changed'):
        work(*args)
    assert ManualEdits(db).get(DiscIdentity('A' * 28, 'disc-a')) == {}


def test_manual_edit_rolls_back_if_session_changes_before_commit(tmp_path):
    db = tmp_path / 'metadata.sqlite3'
    store = ManualEdits(db)
    identity = DiscIdentity('A' * 28, 'disc-a')
    store.set(identity, field='album', value='original')
    checks = iter((True, False))
    with pytest.raises(ValueError, match='disc changed'):
        store.set(identity, field='album', value='stale', current=lambda: next(checks))
    assert store.get(identity) == {'album': 'original'}
