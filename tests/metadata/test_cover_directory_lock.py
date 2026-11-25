"""WEB-07b2f: cover writes and pruning share a Linux advisory lock."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from sonoscdplayer.metadata.cover_lock import locked_cover_directory
from sonoscdplayer.metadata.library_maintenance import CONFIRM_COVERS
from test_library_maintenance import setup


def test_cover_directory_lock_serializes_other_threads(tmp_path):
    entered = Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        def other():
            with locked_cover_directory(tmp_path):
                entered.set()
        with locked_cover_directory(tmp_path):
            job = executor.submit(other)
            assert not entered.wait(0.15)
            assert not job.done()
        job.result(timeout=3)
    assert entered.is_set()


def test_prune_waits_for_cover_writer_lock(tmp_path):
    maintenance, _tokens, _db, covers = setup(tmp_path)
    unused = covers / ('a' * 64 + '.jpg')
    with ThreadPoolExecutor(max_workers=1) as executor:
        with locked_cover_directory(covers):
            job = executor.submit(maintenance.apply, action='prune-auto', confirmation=CONFIRM_COVERS)
            assert not job.done() or unused.exists()
            # The prune must not remove anything while a cover writer holds the lock.
            import time
            time.sleep(0.15)
            assert unused.exists()
        result = job.result(timeout=3)
    assert result['changed_count'] == 1
    assert not unused.exists()
