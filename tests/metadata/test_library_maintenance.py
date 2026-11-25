"""WEB-04c: selection safety, cover references, schema and maintenance mailbox."""
import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import sqlite3
import time

import pytest

from sonoscdplayer.metadata.library_maintenance import (
    LibraryMaintenance, LibraryMaintenanceWorkflow, validate_maintenance,
    CONFIRM_ALL, CONFIRM_COVERS,
)
from sonoscdplayer.metadata.manual_edits import disc_token
from sonoscdplayer.metadata.musicbrainz import DiscIdentity


RELEASE = '01234567-89ab-cdef-0123-456789abcdef'


def setup(tmp_path):
    path = tmp_path / 'metadata.sqlite3'
    cover = tmp_path / 'covers'
    cover.mkdir()
    db = sqlite3.connect(path)
    db.executescript('''PRAGMA user_version=1;
        CREATE TABLE discs (disc_id TEXT NOT NULL,toc TEXT NOT NULL,local_json TEXT NOT NULL,
            release_id TEXT,match_method TEXT,confidence_score INTEGER,
            confirmed_by_user INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            PRIMARY KEY(disc_id,toc));
        CREATE TABLE manual_metadata (disc_id TEXT NOT NULL,toc TEXT NOT NULL,
            fields_json TEXT NOT NULL,PRIMARY KEY(disc_id,toc));''')
    ids = []
    for i in range(3):
        disc_id, toc = str(i) * 28, f'toc-{i}'
        db.execute('INSERT INTO discs VALUES (?,?,?,?,?,?,?,?)',
            (disc_id,toc,json.dumps({'album':f'CD {i}', 'tracks':[{'number':1}]}),
             RELEASE if i < 2 else None, 'manual', None,0,1))
        db.execute('INSERT INTO manual_metadata VALUES (?,?,?)',
            (disc_id,toc,json.dumps({'album':f'Hand {i}'})))
        ids.append(disc_token(DiscIdentity(disc_id, toc)))
    db.commit()
    db.close()
    (cover / (sha256(RELEASE.encode()).hexdigest()+'.jpg')).write_bytes(b'keep-release')
    (cover / ('a'*64+'.jpg')).write_bytes(b'unused')
    (cover / ('manual-'+ids[0]+'.jpg')).write_bytes(b'keep-manual')
    return LibraryMaintenance(path,cover), ids, path, cover


def test_validate_strict_fail_closed():
    for kwargs in ({'action':'unknown'}, {'action':'bulk-delete','tokens':[]},
                   {'action':'bulk-delete','tokens':['a'*32]*2},
                   {'action':'bulk-delete','tokens':['a'*32]*41},
                   {'action':'bulk-delete','tokens':['a'*31]},
                   {'action':'clear-all','confirmation':'yes'},
                   {'action':'prune-auto','confirmation':'DELETE ALL METADATA'},
                   {'action':'stats','tokens':[]}):
        with pytest.raises(ValueError): validate_maintenance(**kwargs)
    assert validate_maintenance('clear-all', confirmation=CONFIRM_ALL)[0]=='clear-all'
    assert validate_maintenance('prune-auto', confirmation=CONFIRM_COVERS)[0]=='prune-auto'


def test_stats_counts_discs_and_files(tmp_path):
    obj, tokens, path, cover = setup(tmp_path)
    stats = obj.stats()
    assert stats['disc_count']==3
    assert stats['auto_cover_count']==2 and stats['manual_cover_count']==1
    assert stats['unused_auto_count']==1 and stats['unused_auto_bytes']==6
    assert stats['database_bytes'] > 0
    assert stats['cover_bytes'] == len(b'keep-release')+6+len(b'keep-manual')
    assert stats['budget_bytes'] > 0
    assert '/tmp' not in str(stats)


def test_bulk_delete_is_atomic_and_keeps_all_covers(tmp_path):
    obj, tokens, path, cover = setup(tmp_path)
    with pytest.raises(ValueError):
        obj.apply(action='bulk-delete', tokens=[tokens[0], 'e'*32])
    assert obj.stats()['disc_count']==3
    result = obj.apply(action='bulk-delete', tokens=[tokens[0],tokens[2]])
    assert result['changed_count']==2
    assert result['stats']['disc_count']==1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM discs').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM manual_metadata').fetchone()[0] == 1
    assert (cover / ('manual-'+tokens[0]+'.jpg')).exists()


def test_clear_all_preserves_all_cover_files(tmp_path):
    obj, tokens, path, cover = setup(tmp_path)
    names = {p.name for p in cover.iterdir()}
    assert obj.apply(action='clear-all', confirmation=CONFIRM_ALL)['changed_count']==3
    assert obj.stats()['disc_count']==0
    assert {p.name for p in cover.iterdir()} == names


def test_prune_keeps_used_and_manual_and_unknown(tmp_path):
    obj, tokens, path, cover = setup(tmp_path)
    (cover / 'unknown.jpg').write_bytes(b'never-delete')
    used = sha256(RELEASE.encode()).hexdigest()+'.jpg'
    result = obj.apply(action='prune-auto', confirmation=CONFIRM_COVERS)
    assert result['changed_count']==1
    assert (cover / used).exists()
    assert (cover / ('manual-'+tokens[0]+'.jpg')).exists()
    assert (cover / 'unknown.jpg').exists()


def test_cleanup_refuses_bad_release_and_symlink(tmp_path):
    obj, _, path, cover = setup(tmp_path)
    (cover / ('b'*64+'.jpg')).symlink_to(cover / ('a'*64+'.jpg'))
    with sqlite3.connect(path) as db:
        db.execute('UPDATE discs SET release_id=?', ('corrupt-id',))
    with pytest.raises(ValueError, match='invalid release'):
        obj.apply(action='prune-auto', confirmation=CONFIRM_COVERS)
    assert (cover / ('a'*64+'.jpg')).exists()


def test_prune_refuses_missing_library_without_deleting_covers(tmp_path):
    obj, _tokens, database, cover = setup(tmp_path)
    automatic = cover / ('a' * 64 + '.jpg')
    database.unlink()
    with pytest.raises(ValueError, match='database unavailable'):
        obj.apply(action='prune-auto', confirmation=CONFIRM_COVERS)
    assert automatic.read_bytes() == b'unused'
    assert any(path.name.startswith('manual-') for path in cover.iterdir())


def test_current_guard_prevents_write(tmp_path):
    obj, _, path, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        obj.apply(action='clear-all', confirmation=CONFIRM_ALL, current=lambda: False)
    assert obj.stats()['disc_count']==3


def test_old_missing_db_returns_empty_stats(tmp_path):
    obj = LibraryMaintenance(tmp_path/'none.sqlite3',tmp_path/'covers')
    assert obj.stats()['disc_count']==0
    assert obj.apply(action='clear-all',confirmation=CONFIRM_ALL)['changed_count']==0


def test_workflow_is_asynchronous_and_must_eject_for_full_reset(tmp_path):
    obj, tokens, path, cover = setup(tmp_path)
    class Parent:
        def library_snapshot(self): return {'ok': True, 'library': {'status':'ready'}}
    class Worker(LibraryMaintenanceWorkflow, Parent):
        pass
    w=Worker()
    with ThreadPoolExecutor(max_workers=1) as executor:
        w._executor=executor
        w._init_library_maintenance(path,cover,None)
        w._last_toc=object()
        w._identify=lambda toc: DiscIdentity('0'*28,'toc-0')
        w._library_manage_job=None
        w._edit_jobs=[]
        w._cover_pending=None
        w._release_selection=None
        with pytest.raises(ValueError, match='eject'):
            w.enqueue_library_maintenance(action='clear-all',confirmation=CONFIRM_ALL)
        with pytest.raises(ValueError, match='inserted'):
            w.enqueue_library_maintenance(action='bulk-delete',tokens=[tokens[0]])
        w._last_toc=None
        w.enqueue_library_maintenance(action='stats')
        assert w.library_snapshot()['library']['maintenance']['status']=='working'
        for _ in range(200):
            if w._poll_library_maintenance(): break
            time.sleep(.005)
        assert w.library_snapshot()['library']['maintenance']['stats']['disc_count']==3
