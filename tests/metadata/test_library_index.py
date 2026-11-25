"""WEB-04a: read-only library, pagination, privacy and asynchronous mailbox."""
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from sonoscdplayer.metadata.library_index import (
    LibraryIndex, LibraryWorkflow, validate_page, valid_token,
)
from sonoscdplayer.metadata.manual_edits import disc_token
from sonoscdplayer.metadata.musicbrainz import DiscIdentity


def setup_db(tmp_path):
    path = tmp_path / 'library.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''PRAGMA user_version=1;
        CREATE TABLE discs (disc_id TEXT NOT NULL,toc TEXT NOT NULL,
            local_json TEXT NOT NULL,release_id TEXT,match_method TEXT,
            confidence_score INTEGER,confirmed_by_user INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,PRIMARY KEY (disc_id,toc));
        CREATE TABLE manual_metadata (disc_id TEXT NOT NULL,toc TEXT NOT NULL,
            fields_json TEXT NOT NULL,PRIMARY KEY (disc_id,toc));''')
    return path, db


def insert(db, disc, toc, album, artist, *, ts, method='unique'):
    tracks = [{'number': i, 'duration_seconds': 120, 'title': f'Song {i}',
               'artist': 'Player', 'isrc': 'SECRET'} for i in (1, 2)]
    data = {'album': album, 'artist': artist, 'mcn': 'secret',
            'tracks': tracks, 'cdtext_status': 'absent'}
    db.execute('''INSERT INTO discs VALUES (?,?,?,?,?,?,?,?)''',
               (disc,toc,json.dumps(data),None,method,None,0,ts))
    return disc_token(DiscIdentity(disc, toc))


def test_page_filter_pagination_and_detail_without_disc(tmp_path):
    path, db = setup_db(tmp_path)
    a = insert(db,'A'*28,'toc 1','Symphonies','Composer',ts=2)
    b = insert(db,'B'*28,'toc 2','Jazz Album','Artist',ts=3)
    c = insert(db,'C'*28,'toc 3','Christmas','Various Artists',ts=4)
    db.commit();db.close()
    index = LibraryIndex(path, tmp_path / 'covers')
    first = index.page(limit=2)
    assert [row['album'] for row in first['entries']] == ['Christmas', 'Jazz Album']
    assert first['total'] == 3
    second = index.page(offset=2,limit=2)
    assert [row['token'] for row in second['entries']] == [a]
    artist = index.page(query='player')
    assert artist['total'] == 3  # search includes track artists
    only = index.page(query='symphonies')
    assert only['total'] == 1 and only['entries'][0]['token'] == a
    detail = index.detail(b)
    assert detail['album'] == 'Jazz Album' and len(detail['tracks']) == 2
    text = json.dumps(detail)
    assert 'toc 2' not in text and 'SECRET' not in text and 'secret' not in text
    assert a != b != c


def test_manual_overlay_and_manual_cover_do_not_mutate_db(tmp_path):
    path, db = setup_db(tmp_path)
    token = insert(db, 'A'*28, 'toc', 'Automatic', 'Original', ts=10)
    db.execute('INSERT INTO manual_metadata VALUES (?,?,?)',
               ('A'*28,'toc',json.dumps({'album':'Manuell','1:track_title':'Mein Lied'})))
    db.commit();db.close()
    cover = tmp_path / 'covers';cover.mkdir()
    (cover / ('manual-'+token+'.jpg')).write_bytes(b'cover')
    idx = LibraryIndex(path,cover)
    page = idx.page(query='Manuell')
    assert page['total'] == 1
    assert page['entries'][0]['manual_cover'] is True
    detail = idx.detail(token)
    assert detail['tracks'][0]['title'] == 'Mein Lied'
    assert detail['album'] == 'Manuell'
    with sqlite3.connect(path) as con:
        assert json.loads(con.execute('SELECT local_json FROM discs').fetchone()[0])['album'] == 'Automatic'


def test_empty_old_schema_and_bad_rows(tmp_path):
    idx = LibraryIndex(tmp_path / 'missing.sqlite3', tmp_path / 'covers')
    assert idx.page()['entries'] == []
    path, db = setup_db(tmp_path)
    db.execute('DROP TABLE manual_metadata')
    insert(db,'A'*28,'toc', 'Good', None,ts=1)
    db.execute('INSERT INTO discs VALUES (?,?,?,?,?,?,?,?)',
               ('B'*28,'toc','{invalid',None,None,None,0,2))
    db.commit();db.close()
    assert LibraryIndex(path,tmp_path/'covers').page()['total'] == 1


@pytest.mark.parametrize('kwargs', [
    {'query': 'x'*81}, {'query': '\n'}, {'limit': 0}, {'limit': 41},
    {'limit': True}, {'offset': -1}, {'offset': 100001}, {'offset': 1.5},
])
def test_page_rejects_invalid_params(kwargs):
    with pytest.raises(ValueError):
        validate_page(**kwargs)


def test_invalid_detail_tokens(tmp_path):
    idx = LibraryIndex(tmp_path/'missing.sqlite3')
    for value in ('', 'A'*32, 'x'*32, 'a'*31, None, '../'+('x'*29)):
        assert not valid_token(value)
        with pytest.raises(ValueError):
            idx.detail(value)


def test_async_library_read_is_cached_and_not_on_caller_thread(tmp_path):
    path, db = setup_db(tmp_path)
    insert(db,'C'*28,'toc','Album','Artist',ts=42)
    db.commit();db.close()
    class Worker(LibraryWorkflow):
        pass
    worker = Worker()
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker._executor = pool
        worker._init_library(path, tmp_path / 'covers')
        worker.enqueue_library(query='', offset=0, limit=20)
        assert worker.library_snapshot()['library']['status'] == 'loading'
        for _ in range(300):
            worker._poll_library()
            if worker.library_snapshot()['library']['status'] == 'ready':
                break
            time.sleep(0.01)
        data = worker.library_snapshot()['library']
        assert data['total'] == 1
        token = data['entries'][0]['token']
        worker.enqueue_library(token=token)
        for _ in range(300):
            worker._poll_library()
            if worker.library_snapshot()['library']['status'] == 'ready':
                break
            time.sleep(0.01)
        assert worker.library_snapshot()['library']['detail']['album'] == 'Album'


def test_manual_only_disc_not_silently_omitted(tmp_path):
    path, db = setup_db(tmp_path)
    db.execute('INSERT INTO manual_metadata VALUES (?,?,?)',
               ('F'*28,'other toc', json.dumps({'album':'Unbekannte CD',
                                               '2:track_title':'Ein Titel'})))
    db.commit();db.close()
    index = LibraryIndex(path)
    data = index.page(query='unbekannte')
    assert data['total'] == 1
    saved = data['entries'][0]
    assert saved['track_count'] is None and saved['cached'] is False
    detail = index.detail(saved['token'])
    assert detail['tracks'][1]['title'] == 'Ein Titel'
