"""AUD-03c: stale release choices must not change the metadata database."""
import pytest

from sonoscdplayer.metadata.cache import DiscCache
from sonoscdplayer.metadata.local_metadata import LocalDiscMetadata, LocalTrackMetadata
from sonoscdplayer.metadata.manual_edits import ManualEdits
from sonoscdplayer.metadata.musicbrainz import DiscIdentity, LookupResult, ReleaseCandidate
from sonoscdplayer.metadata.release_selection import confirm_release
from sonoscdplayer.metadata.worker import MetadataWorker

from test_metadata_worker import FakeToc, ManualExecutor


OLD_RELEASE = 'f9cb3073-ac5d-4747-80e9-b69bcba0f366'
NEW_RELEASE = '0f9cb307-3ac5-4d47-80e9-b69bcba0f366'


def candidate():
    return ReleaseCandidate(NEW_RELEASE, 'New album', 'New artist', None, None,
                            ('New title',))


def test_release_selection_rollback_if_disc_changes_before_commit(tmp_path):
    cache = DiscCache(tmp_path / 'metadata.sqlite3')
    identity = DiscIdentity('A' * 28, 'first-disc')
    original = LocalDiscMetadata('Original', 'Artist',
                                 (LocalTrackMetadata(1, 100, 'Original song'),), 'unavailable')
    cache.put(identity, original, release_id=OLD_RELEASE, match_method='manual',
              confirmed_by_user=True)
    manual = ManualEdits(cache.path)
    manual.set(identity, field='album', value='Protected')
    checks = iter((True, False))
    with pytest.raises(ValueError, match='disc changed'):
        confirm_release(cache, identity, original, candidate(),
                        keep_manual=False, current=lambda: next(checks))
    assert cache.get(identity).release_id == OLD_RELEASE
    assert manual.get(identity) == {'album': 'Protected'}


@pytest.mark.parametrize('reinsert_same_toc', [False, True])
def test_queued_release_selection_does_not_write_after_disc_change(
        tmp_path, reinsert_same_toc):
    executor = ManualExecutor()
    cache_file = tmp_path / 'metadata.sqlite3'
    worker = MetadataWorker(
        cache_path=cache_file, cover_dir=tmp_path / 'covers',
        executor=executor,
        identify=lambda toc: DiscIdentity('A' * 28, toc.label),
        lookup=lambda *args, **kwargs: LookupResult('not_found', ()))
    original = FakeToc('disc-A')
    worker.observe(original)
    executor.finish(0)
    worker.poll(original)
    token = worker.snapshot['edit_token']
    worker._release_items = {NEW_RELEASE: candidate()}
    before = DiscCache(cache_file).get(DiscIdentity('A' * 28, 'disc-A'))
    worker.enqueue_release_selection(token=token, release_id=NEW_RELEASE,
                                     keep_manual=False)
    # The scheduled selection has not run yet.
    assert len(executor.jobs) == 2
    worker.observe(None if reinsert_same_toc else FakeToc('disc-B'))
    if reinsert_same_toc:
        worker.observe(FakeToc('disc-A'))
    future, task, args = executor.jobs[1]
    with pytest.raises(ValueError, match='disc changed'):
        task(*args)
    # A discarded stale job must leave the original cache association intact.
    assert DiscCache(cache_file).get(DiscIdentity('A' * 28, 'disc-A')) == before
    assert worker._release_selection is None


def test_release_selection_can_commit_when_disc_is_current(tmp_path):
    cache = DiscCache(tmp_path / 'metadata.sqlite3')
    identity = DiscIdentity('A' * 28, 'first-disc')
    original = LocalDiscMetadata('Original', 'Artist',
                                 (LocalTrackMetadata(1, 100, 'Original song'),), 'unavailable')
    cache.put(identity, original, release_id=OLD_RELEASE, match_method='manual',
              confirmed_by_user=True)
    result = confirm_release(cache, identity, original, candidate(),
                             keep_manual=False, current=lambda: True)
    assert result == NEW_RELEASE
    assert cache.get(identity).release_id == NEW_RELEASE
