"""AUD-03e: stale cover completions must not update a reinserted CD."""
from concurrent.futures import Future

from sonoscdplayer.metadata.manual_cover import ManualCoverWorkflow
from sonoscdplayer.metadata.manual_edits import disc_token


class ManualExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, func):
        result = Future()
        self.jobs.append((result, func))
        return result


class StubCover(ManualCoverWorkflow):
    def __init__(self, tmp_path):
        (tmp_path / 'covers').mkdir()
        self._executor = ManualExecutor()
        self._last_toc = object()
        self._identify = lambda _toc: type('Identity', (), {
            'disc_id': 'A' * 28, 'toc': 'same-toc'})()
        self._init_manual_covers(tmp_path / 'covers',
                                 tmp_path / 'ui.toml', tmp_path / 'stage')
        self._result = {'edit_token': disc_token(self._identify(None))}


def test_old_cover_error_is_not_applied_after_same_disc_reinsertion(tmp_path):
    worker = StubCover(tmp_path)
    token = worker._result['edit_token']
    worker.enqueue_cover(token=token, remove=True)
    stale, work = worker._executor.jobs[0]
    worker._reset_manual_covers()
    worker._result = {'edit_token': token}  # Identical CD, new insertion.
    try:
        work()
    except ValueError as exc:
        stale.set_exception(exc)
    else:
        stale.set_result(None)
    assert worker._poll_manual_covers() is False
    assert worker._cover_error is None
    assert worker._cover_seq == 1
    assert worker._cover_pending is None


def test_current_cover_completion_still_applies(tmp_path):
    worker = StubCover(tmp_path)
    worker.enqueue_cover(token=worker._result['edit_token'], remove=True)
    future, work = worker._executor.jobs[0]
    future.set_result(work())
    assert worker._poll_manual_covers() is True
    assert worker._cover_error is None
    assert worker._cover_pending is None
