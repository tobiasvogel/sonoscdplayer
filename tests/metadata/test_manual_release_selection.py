"""WEB-03b: user-selected release, CD-TEXT priority and per-disc persistence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace
import time

import pytest

from sonoscdplayer.metadata.cache import DiscCache
from sonoscdplayer.metadata.local_metadata import LocalDiscMetadata, LocalTrackMetadata
from sonoscdplayer.metadata.manual_edits import ManualEdits, disc_token
from sonoscdplayer.metadata.musicbrainz import DiscIdentity
from sonoscdplayer.metadata.release_matching import MatchingPolicy
from sonoscdplayer.metadata.release_selection import (
    confirm_release, release_detail, release_summaries, selected_metadata, valid_release_id)
from sonoscdplayer.metadata.release_workflow import ReleaseWorkflow

FIRST = '00000000-0000-4000-8000-000000000001'
SECOND = '00000000-0000-4000-8000-000000000002'


def local(cdtext='absent'):
    return LocalDiscMetadata('CD title' if cdtext == 'available' else None,
        'CD artist' if cdtext == 'available' else None,
        (LocalTrackMetadata(1, 120., 'CD track' if cdtext == 'available' else None,
                            'CD singer' if cdtext == 'available' else None, 'ISRC01'),
         LocalTrackMetadata(2, 130., None, None, 'ISRC02')),
        cdtext)


def candidate(release_id=FIRST, titles=('Release 1','Release 2')):
    return SimpleNamespace(release_id=release_id, title='Release album',
        artist='Release artist', country='CH', date='2026-01-01',
        track_titles=titles, track_artists=('Performer 1', 'Performer 2'))


def test_release_selection_protects_cdtext_and_maps_by_track_number():
    changed = selected_metadata(local('available'), candidate(titles=('Other 1',)))
    assert changed.album == 'CD title' and changed.artist == 'CD artist'
    assert changed.tracks[0].title == 'CD track'
    assert changed.tracks[0].artist == 'CD singer'
    assert changed.tracks[1].title is None  # no stale automatic value
    assert changed.tracks[0].isrc == 'ISRC01'
    assert changed.tracks[1].duration_seconds == 130.


def test_explicit_release_overwrites_prior_automatic_names():
    changed = selected_metadata(local(), candidate(titles=('New A',)))
    assert changed.album == 'Release album'
    assert [track.title for track in changed.tracks] == ['New A', None]


def test_manual_confirmation_can_change_a_previously_confirmed_release(tmp_path):
    identity = DiscIdentity('A' * 28, 'first TOC')
    cache = DiscCache(tmp_path / 'cache.sqlite3')
    confirm_release(cache, identity, local(), candidate(FIRST), keep_manual=True)
    confirm_release(cache, identity, local(), candidate(SECOND), keep_manual=True)
    with cache._connect() as db:
        row = db.execute('SELECT release_id,match_method,confirmed_by_user FROM discs').fetchone()
    assert row == (SECOND, 'manual', 1)


def test_manual_edits_are_retained_or_discarded_explicitly(tmp_path):
    identity = DiscIdentity('A' * 28, 'TOC')
    path = tmp_path / 'cache.sqlite3'
    store = ManualEdits(path)
    store.set(identity, field='album', value='My title')
    confirm_release(DiscCache(path), identity, local(), candidate(), keep_manual=True)
    assert store.get(identity)['album'] == 'My title'
    confirm_release(DiscCache(path), identity, local(), candidate(SECOND), keep_manual=False)
    assert store.get(identity) == {}


def test_other_disc_manual_corrections_are_never_deleted(tmp_path):
    path = tmp_path / 'cache.sqlite3'
    store = ManualEdits(path)
    a, b = DiscIdentity('A'*28, 'toc-a'), DiscIdentity('A'*28, 'toc-b')
    store.set(a, field='album', value='A')
    store.set(b, field='album', value='B')
    confirm_release(DiscCache(path), a, local(), candidate(), keep_manual=False)
    assert store.get(a) == {} and store.get(b)['album'] == 'B'


def test_releases_have_valid_identifiers_and_bounded_details():
    assert valid_release_id(FIRST)
    assert not valid_release_id('RINCON_ABC')
    detail = release_detail(candidate(titles=('X'*500, None)))
    assert len(detail['tracks'][0]['title']) == 160
    assert detail['tracks'][1]['title'] is None
    assert len(release_summaries([candidate()], local(), MatchingPolicy())) == 1


def workflow(tmp_path, *, overrides=()):
    identity = DiscIdentity('A'*28, 'toc-a')
    token = disc_token(identity)
    w = ReleaseWorkflow()
    w._init_releases()
    w._last_toc = SimpleNamespace(tracks=[object(), object()])
    w._last_cdtext = None
    w._result = {'edit_token': token, 'manual_fields': list(overrides),
                 '_automatic': {'tracks': [{'number':1,'title':'Release 1'}, {'number':2,'title':'Release 2'}]}}
    w._identify = lambda toc: identity
    w._timeout = 8
    w._policy = MatchingPolicy()
    w._cache = DiscCache(tmp_path/'cache.sqlite3')
    w._edit_jobs = []
    w._pending = w._pending_toc = None
    w._retry_at = None
    w._submit = lambda toc: setattr(w, 'reload_called', True)
    w.reload_called = False
    w._lookup = lambda identity, **kw: SimpleNamespace(
        status='ambiguous', releases=(candidate(), candidate(SECOND)))
    return w, token


def finish(w):
    for _ in range(200):
        if w._poll_releases(w._last_toc):
            return
        time.sleep(.005)
    raise AssertionError('background operation did not finish')


def test_worker_lookups_are_async_and_disc_qualified(tmp_path):
    w, token = workflow(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as executor:
        w._executor = executor
        with pytest.raises(ValueError, match='disc changed'):
            w.enqueue_release_search(token='0'*32)
        w.enqueue_release_search(token=token)
        assert w._release_info['status'] == 'searching'
        finish(w)
        assert w._release_info['status'] == 'ready'
        assert len(w._release_info['candidates']) == 2
        assert w.release_detail_for(token=token, release_id=SECOND)['title'] == 'Release album'
        with pytest.raises(ValueError):
            w.release_detail_for(token=token, release_id='0'*36)


def test_worker_requires_manual_edit_decision_and_preserves_disc_identity(tmp_path):
    w, token = workflow(tmp_path, overrides=('1:track_title',))
    with ThreadPoolExecutor(max_workers=1) as executor:
        w._executor = executor
        w.enqueue_release_search(token=token)
        finish(w)
        w._release_items[FIRST] = candidate(titles=('only one',))
        with pytest.raises(ValueError, match='cannot safely map track edits'):
            w.enqueue_release_selection(token=token, release_id=FIRST, keep_manual=True)
        with pytest.raises(ValueError, match='manual corrections'):
            w.enqueue_release_selection(token=token, release_id=FIRST, keep_manual='yes')
        w.enqueue_release_selection(token=token, release_id=FIRST, keep_manual=False)
        finish(w)
        assert w.reload_called is True
        assert w._result is None


def test_worker_disc_change_discards_old_search_completion(tmp_path):
    w, token = workflow(tmp_path)
    import threading
    gate = threading.Event()
    def lookup(identity, **kw):
        gate.wait(2)
        return SimpleNamespace(status='unique', releases=(candidate(),))
    w._lookup = lookup
    with ThreadPoolExecutor(max_workers=1) as executor:
        w._executor = executor
        w.enqueue_release_search(token=token)
        w._reset_releases()  # observe() does this on a verified TOC change
        w._result = {'edit_token': 'b'*32}
        gate.set()
        executor.shutdown(wait=True)
        assert w._poll_releases(w._last_toc) is False
        assert w._release_items == {}


def test_track_manual_edits_are_not_silently_mapped_to_reordered_titles(tmp_path):
    w, token = workflow(tmp_path, overrides=('1:track_title',))
    w._release_items = {FIRST: candidate(titles=('Release 1', 'Release 2'))}
    assert w._can_keep_track_edits(w._release_items[FIRST]) is True
    swapped = candidate(titles=('Release 2', 'Release 1'))
    assert w._can_keep_track_edits(swapped) is False
    with pytest.raises(ValueError, match='manual corrections'):
        w.enqueue_release_selection(token=token, release_id=FIRST, keep_manual='yes')
    w._release_items[FIRST] = swapped
    with pytest.raises(ValueError, match='cannot safely map'):
        w.enqueue_release_selection(token=token, release_id=FIRST, keep_manual=True)


def test_offline_lookup_returns_public_error_without_releasing_private_data(tmp_path):
    w, token = workflow(tmp_path)
    w._lookup = lambda identity, **kw: SimpleNamespace(status='offline', releases=(), error='private details')
    with ThreadPoolExecutor(max_workers=1) as executor:
        w._executor = executor
        w.enqueue_release_search(token=token)
        with pytest.raises(RuntimeError, match='still pending'):
            w.enqueue_release_search(token=token)
        finish(w)
        assert w._release_info['status'] == 'error'
        assert 'private details' not in str(w._release_snapshot())
        assert w._release_info['candidates'] == []
