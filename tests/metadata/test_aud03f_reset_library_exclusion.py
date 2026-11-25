"""AUD-03f: background reset and library writes must exclude one another."""
import pytest

from sonoscdplayer.metadata.manual_edits import disc_token
from sonoscdplayer.metadata.musicbrainz import DiscIdentity
from sonoscdplayer.metadata.worker import MetadataWorker
from test_metadata_worker import FakeToc, ManualExecutor


def test_pending_reset_blocks_library_manage_and_destructive_maintenance(tmp_path):
    executor = ManualExecutor()
    worker = MetadataWorker(
        cache_path=tmp_path / 'metadata.sqlite3',
        cover_dir=tmp_path / 'covers', executor=executor,
        online=False, identify=lambda toc: DiscIdentity('A' * 28, toc.label))
    toc = FakeToc('inserted')
    worker.observe(toc)
    executor.finish(0)
    worker.poll(toc)
    assert worker.reset_current()
    assert len(executor.jobs) == 2

    # An offline library entry is editable even with a disc inserted, but
    # must not be modified while that disc's reset is still queued.
    other = disc_token(DiscIdentity('B' * 28, 'offline'))
    with pytest.raises(RuntimeError, match='disc reset still pending'):
        worker.enqueue_library_manage(token=other, action='delete')
    with pytest.raises(RuntimeError, match='metadata write pending'):
        worker.enqueue_library_maintenance(
            action='bulk-delete', tokens=[other])

    # Neither rejected request may enqueue a database operation.
    assert len(executor.jobs) == 2
    assert worker._library_manage_job is None
    assert worker._maintenance_job is None

    executor.finish(1)
    worker.poll(toc)
    # The mailbox is free again after the reset has been consumed.
    # A read-only maintenance request remains allowed throughout.
    worker.enqueue_library_maintenance(action='stats')
    assert worker._maintenance_job is not None
