"""WEB-03a: disc-qualified manual values survive automatic metadata changes."""
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from sonoscdplayer.metadata.manual_edits import ManualEdits, apply_edits, disc_token, validate_edit
from sonoscdplayer.metadata.musicbrainz import DiscIdentity
from sonoscdplayer.metadata.worker import MetadataWorker


def sample():
    return {'album': 'Original album', 'artist': 'Original artist',
            'tracks': [{'number': 1, 'title': 'First', 'artist': 'Artist 1'},
                       {'number': 2, 'title': None, 'artist': None}],
            'match_status': 'cached', 'metadata_source': 'musicbrainz'}


def test_manual_fields_are_isolated_by_disc_id_and_full_toc(tmp_path):
    store = ManualEdits(tmp_path / 'disc.sqlite3')
    a = DiscIdentity('A' * 28, 'toc-A')
    b = DiscIdentity('A' * 28, 'toc-B')
    c = DiscIdentity('B' * 28, 'toc-A')
    store.set(a, field='album', value='Edited album')
    store.set(a, field='track_title', track=2, value='Edited track')
    assert store.get(a) == {'album': 'Edited album', '2:track_title': 'Edited track'}
    assert store.get(b) == {} and store.get(c) == {}
    assert len(disc_token(a)) == 32 and disc_token(a) != disc_token(b)
    assert ManualEdits(tmp_path / 'disc.sqlite3').get(a) == store.get(a)


def test_manual_override_wins_and_undo_restores_automatic_values(tmp_path):
    identity = DiscIdentity('A' * 28, 'toc')
    store = ManualEdits(tmp_path / 'db.sqlite3')
    store.set(identity, field='album', value='User album')
    store.set(identity, field='track_artist', track=1, value='User artist')
    automatic = sample()
    corrected = apply_edits(automatic, store.get(identity))
    assert corrected['album'] == 'User album'
    assert corrected['tracks'][0]['artist'] == 'User artist'
    assert automatic['album'] == 'Original album'
    assert corrected['metadata_source'] == 'manual'
    assert corrected['manual_fields'] == ['1:track_artist', 'album']
    store.set(identity, field='album', value=None)
    store.set(identity, field='track_artist', track=1, value=None)
    restored = apply_edits(corrected, store.get(identity))
    assert restored['album'] == 'Original album'
    assert restored['tracks'][0]['artist'] == 'Artist 1'
    assert restored['manual_fields'] == []
    assert restored['metadata_source'] != 'manual'
    assert store.delete(identity) is False  # empty after individual undo


@pytest.mark.parametrize('field,track,value', [
    ('bad', None, 'A'), ('album', 1, 'A'), ('track_title', None, 'A'),
    ('track_artist', True, 'A'), ('track_title', 100, 'A'),
    ('album', None, ''), ('artist', None, ' \t '),
    ('album', None, 'X' * 161), ('artist', None, 'line\nbreak'),
])
def test_bad_edits_rejected(field, track, value):
    with pytest.raises(ValueError):
        validate_edit(field, track, value)


def test_player_worker_schedules_edits_off_thread_and_rejects_stale_tokens(tmp_path):
    identity = DiscIdentity('A' * 28, 'toc-A')
    worker = object.__new__(MetadataWorker)
    worker._last_toc = object()
    worker._identify = lambda _toc: identity
    worker._manual = ManualEdits(tmp_path / 'db.sqlite3')
    worker._result = {**sample(), 'edit_token': disc_token(identity)}
    worker._edit_jobs = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker._executor = executor
        with pytest.raises(ValueError, match='disc changed'):
            worker.enqueue_edit(token='0' * 32, field='album', value='wrong disc')
        worker.enqueue_edit(token=disc_token(identity), field='album', value='Corrected')
        assert worker._edit_jobs  # queued; not run synchronously on caller
        for _ in range(300):
            if worker._consume_edits():
                break
            time.sleep(0.01)
        assert worker._result['album'] == 'Corrected'
        assert worker._result['metadata_source'] == 'manual'
        assert not worker._edit_jobs
        assert worker._manual.get(identity)['album'] == 'Corrected'
