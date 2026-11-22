"""Explicit, disc-qualified MusicBrainz release choices (WEB-03b).

A release choice is a deliberate *user* action, not another automatic match:
CD-TEXT and manually protected fields remain authoritative.  SQLite writes
must run on the metadata thread, never the player/control thread.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import re
import time

from sonoscdplayer.metadata.local_metadata import LocalDiscMetadata
from sonoscdplayer.metadata.release_matching import score_release

_RELEASE_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def valid_release_id(value):
    return type(value) is str and _RELEASE_ID.fullmatch(value) is not None


def release_summaries(releases, local, policy):
    """Only bounded, display-worthy labels; track details stay on the player."""
    scored = sorted((score_release(local, item, policy) for item in releases
                     if valid_release_id(item.release_id)),
                    key=lambda item: (-item.score, item.release.release_id))
    return [{
        'release_id': item.release.release_id,
        'title': item.release.title[:160],
        'artist': (item.release.artist or '')[:160],
        'country': (item.release.country or '')[:40],
        'date': (item.release.date or '')[:40],
        'score': item.score,
        'track_count': len(item.release.track_titles),
    } for item in scored[:100]]


def release_detail(candidate):
    """Bound one chosen release's detail for the web; no raw provider JSON."""
    return {
        'release_id': candidate.release_id,
        'title': candidate.title[:160],
        'artist': (candidate.artist or '')[:160],
        'country': (candidate.country or '')[:40],
        'date': (candidate.date or '')[:40],
        'track_count': len(candidate.track_titles),
        'tracks': [
            {'number': i + 1, 'title': title[:160] if isinstance(title, str) else None,
             'artist': (candidate.track_artists[i][:160]
                        if len(candidate.track_artists) > i and
                           isinstance(candidate.track_artists[i], str) else None)}
            for i, title in enumerate(candidate.track_titles[:99])
        ],
    }


def selected_metadata(cdtext_local: LocalDiscMetadata, candidate):
    """D-128–130: map by track number, clear obsolete automatic names.

    Protect CD-TEXT (not the prior automatic cache) and preserve TOC-derived
    track numbers, durations, ISRCs and MCN. Explicit manual field overrides
    are stored in a separate table and applied *after* this selection.
    """
    if not valid_release_id(candidate.release_id):
        raise ValueError('invalid release ID')
    preserve_cdtext = cdtext_local.cdtext_status == 'available'
    tracks = []
    for i, track in enumerate(cdtext_local.tracks):
        title = candidate.track_titles[i] if i < len(candidate.track_titles) else None
        artist = candidate.track_artists[i] if i < len(candidate.track_artists) else None
        tracks.append(replace(track,
                              title=(track.title if preserve_cdtext and track.title else title),
                              artist=(track.artist if preserve_cdtext and track.artist else artist)))
    return replace(cdtext_local,
                   album=(cdtext_local.album if preserve_cdtext and cdtext_local.album
                          else candidate.title),
                   artist=(cdtext_local.artist if preserve_cdtext and cdtext_local.artist
                           else candidate.artist),
                   tracks=tuple(tracks))


def confirm_release(cache, identity, cdtext_local, candidate, *, keep_manual):
    """Atomically rebind a disc, explicitly allowing re-selection of a manual ID.

    `DiscCache.put` rightly refuses an *automatic* overwrite of a confirmed
    release. This dedicated, explicit operation may replace that binding.
    With keep_manual=False the matching field overrides are removed within
    the same transaction, so crashes cannot leave a half-updated selection.
    """
    if type(keep_manual) is not bool:
        raise ValueError('explicit manual correction decision required')
    local = selected_metadata(cdtext_local, candidate)
    db = cache._connect(write=True)
    try:
        db.execute('BEGIN IMMEDIATE')
        if not keep_manual:
            found = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='manual_metadata'").fetchone()
            if found:
                db.execute('DELETE FROM manual_metadata WHERE disc_id=? AND toc=?',
                           (identity.disc_id, identity.toc))
        db.execute('''INSERT INTO discs
            (disc_id,toc,local_json,release_id,match_method,confidence_score,
             confirmed_by_user,updated_at) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(disc_id,toc) DO UPDATE SET
                local_json=excluded.local_json,
                release_id=excluded.release_id,
                match_method=excluded.match_method,
                confidence_score=excluded.confidence_score,
                confirmed_by_user=excluded.confirmed_by_user,
                updated_at=excluded.updated_at''',
            (identity.disc_id, identity.toc,
             json.dumps(asdict(local), ensure_ascii=False, separators=(',', ':')),
             candidate.release_id, 'manual', None, 1, int(time.time())))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return candidate.release_id
