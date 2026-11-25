"""WEB-04b: offline edits are transactional, disc scoped and audio independent."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from sonoscdplayer.metadata.library_index import LibraryIndex, LibraryWorkflow
from sonoscdplayer.metadata.library_manage import (
    LibraryManageWorkflow, StoredLibraryManager, validate_manage,
)
from sonoscdplayer.metadata.manual_edits import disc_token
from sonoscdplayer.metadata.musicbrainz import DiscIdentity


def database(tmp_path):
    path = tmp_path / 'library.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''PRAGMA user_version=1;
        CREATE TABLE discs (disc_id TEXT NOT NULL,toc TEXT NOT NULL,
            local_json TEXT NOT NULL,release_id TEXT,match_method TEXT,
            confidence_score INTEGER,confirmed_by_user INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,PRIMARY KEY (disc_id,toc));
        CREATE TABLE manual_metadata (disc_id TEXT NOT NULL,toc TEXT NOT NULL,
            fields_json TEXT NOT NULL,PRIMARY KEY (disc_id,toc));''')
    identity = DiscIdentity('A' * 28, 'known toc')
    other = DiscIdentity('B' * 28, 'another toc')
    def insert(one, title):
        data = {'album': title, 'artist': 'Original', 'cdtext_status': 'absent',
                'tracks': [{'number': i, 'title': f'Track {i}', 'artist': 'Band',
                            'duration_seconds': 120, 'isrc': None} for i in (1, 2)]}
        db.execute('INSERT INTO discs VALUES (?,?,?,?,?,?,?,?)',
                   (one.disc_id, one.toc, json.dumps(data), None, None, None, 0, 20))
    insert(identity, 'First CD')
    insert(other, 'Second CD')
    db.commit(); db.close()
    return path, identity, other


def test_atomic_batch_changes_both_fields_and_does_not_touch_base_cache(tmp_path):
    path, identity, other = database(tmp_path)
    token = disc_token(identity)
    manager = StoredLibraryManager(path)
    _, _, fields = validate_manage(token, 'edit', [
        {'field':'album', 'value':'Korrigiertes Album'},
        {'field':'track_title', 'track':1, 'value':'Mein Lied'},
    ])
    manager.apply(token=token, action='edit', changes=fields)
    idx = LibraryIndex(path)
    detail = idx.detail(token)
    assert detail['album'] == 'Korrigiertes Album'
    assert detail['tracks'][0]['title'] == 'Mein Lied'
    assert idx.detail(disc_token(other))['album'] == 'Second CD'
    with sqlite3.connect(path) as db:
        assert json.loads(db.execute('SELECT local_json FROM discs WHERE disc_id=?',
                                     (identity.disc_id,)).fetchone()[0])['album'] == 'First CD'


def test_invalid_edit_rolls_back_every_field(tmp_path):
    path, identity, _ = database(tmp_path)
    manager = StoredLibraryManager(path)
    token = disc_token(identity)
    with pytest.raises(ValueError, match='track not in'):
        manager.apply(token=token, action='edit', changes=(
            ('album', None, 'Would have changed'), ('track_title', 3, 'Wrong track')))
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM manual_metadata').fetchone()[0] == 0


def test_reset_preserves_manual_edits_and_cover(tmp_path):
    path, identity, _ = database(tmp_path)
    token = disc_token(identity)
    store = StoredLibraryManager(path)
    store.apply(token=token, action='edit', changes=(('album', None, 'Keep me'),))
    covers = tmp_path / 'covers'; covers.mkdir()
    file = covers / f'manual-{token}.jpg'; file.write_bytes(b'cover')
    store.apply(token=token, action='reset')
    assert file.read_bytes() == b'cover'
    detail = LibraryIndex(path, covers).detail(token)
    assert detail['album'] == 'Keep me' and detail['cached'] is False
    assert detail['release_id'] is None
    assert detail['manual_cover'] is True


def test_clear_manual_preserves_cached_entry(tmp_path):
    path, identity, _ = database(tmp_path)
    token = disc_token(identity)
    manager = StoredLibraryManager(path)
    manager.apply(token=token, action='edit', changes=(('artist', None, 'Other artist'),))
    manager.apply(token=token, action='clear-manual')
    result = LibraryIndex(path).detail(token)
    assert result['artist'] == 'Original' and result['cached'] is True


def test_delete_exact_entry_and_leave_shared_cover_files(tmp_path):
    path, identity, other = database(tmp_path)
    token = disc_token(identity)
    manager = StoredLibraryManager(path)
    manager.apply(token=token, action='edit', changes=(('artist', None, 'Manual'),))
    covers = tmp_path / 'covers'; covers.mkdir()
    (covers / f'manual-{token}.jpg').write_bytes(b'manual cover')
    (covers / 'shared.jpg').write_bytes(b'other cover')
    manager.apply(token=token, action='delete')
    with pytest.raises(ValueError, match='not found'):
        LibraryIndex(path).detail(token)
    assert LibraryIndex(path).detail(disc_token(other))['album'] == 'Second CD'
    assert (covers / f'manual-{token}.jpg').is_file()
    assert (covers / 'shared.jpg').is_file()


def test_untrusted_tokens_cannot_delete_anything(tmp_path):
    path, identity, _ = database(tmp_path)
    store = StoredLibraryManager(path)
    with pytest.raises(ValueError):
        store.apply(token='0' * 32, action='delete')
    assert LibraryIndex(path).detail(disc_token(identity))['cached']


def test_validation_rejects_extra_fields_duplicate_tracks_and_invalid_actions():
    token = 'a' * 32
    for action in ('wipe', '', None, True):
        with pytest.raises(ValueError): validate_manage(token, action)
    with pytest.raises(ValueError): validate_manage(token, 'delete', [])
    with pytest.raises(ValueError): validate_manage(token, 'edit', [])
    with pytest.raises(ValueError): validate_manage(token, 'edit', [
        {'field':'album','value':'First'}, {'field':'album','value':'Second'}])
    with pytest.raises(ValueError): validate_manage(token, 'edit', [
        {'field':'album','value':'Title','path':'private'}])
    with pytest.raises(ValueError): validate_manage(token, 'edit', [
        {'field':'track_title','track':True,'value':'Title'}])


def test_async_status_and_active_disc_rejection(tmp_path):
    path, identity, _ = database(tmp_path)
    class Worker(LibraryManageWorkflow, LibraryWorkflow):
        pass
    worker = Worker()
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker._executor = executor
        worker._init_library(path, tmp_path / 'covers')
        worker._init_library_manage(path)
        worker._last_toc = object()
        worker._identify = lambda _: identity
        with pytest.raises(ValueError, match='inserted disc'):
            worker.enqueue_library_manage(token=disc_token(identity), action='delete')
        worker._last_toc = None
        worker.enqueue_library_manage(token=disc_token(identity), action='edit',
                                      changes=[{'field':'album','value':'Async edit'}])
        assert worker.library_snapshot()['library']['manage']['status'] == 'saving'
        assert worker._library_manage_job.result(timeout=3)['action'] == 'edit'
        assert worker._poll_library_manage()
        assert worker.library_snapshot()['library']['manage']['status'] == 'ready'
        assert LibraryIndex(path).detail(disc_token(identity))['album'] == 'Async edit'
