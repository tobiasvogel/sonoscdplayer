"""WEB-05e3: stale writes must be rejected across independent processes."""
import multiprocessing

import pytest

from sonoscdplayer.player.playback_settings import (
    FILE_NAME, LOCK_FILE, SettingsConflict, read_overrides, update,
)

BASE = {'streaming': {'max_buffer_mib': 16},
        'sonos': {'use_queue': True}, 'playback': {'continue': True}}


def _writer(state, start, results, buffer):
    values = {'sonos.use_queue': False, 'playback.continue': False,
              'streaming.max_buffer_mib': buffer}
    start.wait(timeout=5)
    try:
        revision = update(BASE, state, {'action': 'save', 'revision': 'none',
                                        'values': values})
        results.put(('saved', buffer, revision))
    except SettingsConflict:
        results.put(('conflict', buffer, None))
    except BaseException as exc:
        results.put(('error', buffer, repr(exc)))


def test_two_processes_cannot_save_same_revision(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    start = ctx.Event()
    results = ctx.Queue()
    workers = [ctx.Process(target=_writer, args=(str(tmp_path), start, results, mib))
               for mib in (64, 128)]
    for worker in workers:
        worker.start()
    try:
        start.set()
        received = [results.get(timeout=12) for _ in workers]
        assert sorted(item[0] for item in received) == ['conflict', 'saved']
        winner = next(item for item in received if item[0] == 'saved')
        current, revision = read_overrides(tmp_path)
        assert current['streaming.max_buffer_mib'] == winner[1]
        assert revision == winner[2]
        assert (tmp_path / LOCK_FILE).is_file()
    finally:
        for worker in workers:
            worker.join(timeout=5)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
    assert all(worker.exitcode == 0 for worker in workers)


def test_lock_symlink_is_never_followed(tmp_path):
    target = tmp_path / 'protected'
    target.write_text('untouched')
    (tmp_path / LOCK_FILE).symlink_to(target)
    with pytest.raises(OSError):
        update(BASE, tmp_path, {'action': 'save', 'revision': 'none', 'values': {
            'sonos.use_queue': False, 'playback.continue': False,
            'streaming.max_buffer_mib': 64}})
    assert target.read_text() == 'untouched'
    assert not (tmp_path / FILE_NAME).exists()
