"""Conservative GnuDB fallback, without MusicBrainz IDs, cache writes or I/O on import.

Only one exact CDDB query match with an independently verified full TOC is
eligible. Local CD-TEXT is never replaced; conflicting local evidence refuses
an automatic assignment. Network calls are injectable for offline tests.
"""
from __future__ import annotations

from dataclasses import replace

from sonoscdplayer.metadata.gnudb import (
    CDDBEntry, CDDBError, identity_from_toc, query_disc, read_entry,
)
from sonoscdplayer.metadata.local_metadata import LocalDiscMetadata


def _compatible(local: LocalDiscMetadata, entry: CDDBEntry) -> bool:
    """Avoid accepting a collision that disagrees with CD-TEXT evidence."""
    if (len(local.tracks) != len(entry.track_titles) or
            len(local.tracks) != len(entry.track_artists)):
        return False
    def normalized(value):
        return ' '.join(value.casefold().split()) if value else None
    if local.album and entry.album and normalized(local.album) != normalized(entry.album):
        return False
    if local.artist and entry.artist and normalized(local.artist) != normalized(entry.artist):
        return False
    for track, title in zip(local.tracks, entry.track_titles):
        if track.title and normalized(track.title) != normalized(title):
            return False
    return True


def resolve_fallback(toc, local: LocalDiscMetadata, *, timeout_seconds=5,
                     query=query_disc, read=read_entry, endpoint=None, contact_email=''):
    """Return (metadata, status) or (None, sanitized status).

    The status is informative only; no CDDB ID is ever used as a MusicBrainz
    release_id and results must not be cached without a provider-aware schema.
    """
    if not isinstance(local, LocalDiscMetadata):
        raise ValueError('expected local disc metadata')
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
        raise ValueError('invalid GnuDB timeout')
    try:
        identity = identity_from_toc(toc)
        kwargs = {'timeout_seconds': timeout_seconds}
        if endpoint is not None:
            kwargs['endpoint'] = endpoint
        if contact_email:
            kwargs['contact_email'] = contact_email
        found = query(identity, **kwargs)
        if found.status != 'unique' or len(found.matches) != 1:
            return None, found.status
        hit = found.matches[0]
        # Even a unique CDDB query can be based on an inexact/colliding ID.
        if hit.disc_id != identity.disc_id:
            return None, 'unverified'
        entry = read(identity, hit, **kwargs)
        if (entry.disc_id != identity.disc_id or entry.category != hit.category or
                not _compatible(local, entry)):
            return None, 'unverified'
        merged = replace(local,
            album=local.album or entry.album,
            artist=local.artist or entry.artist,
            tracks=tuple(replace(track, title=track.title or title,
                                 artist=track.artist or artist)
                         for track, title, artist in zip(
                             local.tracks, entry.track_titles, entry.track_artists)))
        return merged, 'unique'
    except (CDDBError, ValueError, TypeError, AttributeError):
        return None, 'unverified'
    except Exception:
        # Fallback is never allowed to interfere with normal CD playback.
        return None, 'error'
