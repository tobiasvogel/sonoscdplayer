"""Non-blocking metadata lifecycle for a verified audio TOC.

No optical-drive reads or Sonos operations. A single background worker handles
SQLite, MusicBrainz and optional cover files. The player worker alone applies
completed results, and only while the identical TOC remains selected.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

from disc_cache import DiscCache, DEFAULT_DB_PATH
from disc_covers import CoverCache, DEFAULT_COVER_DIR
import time

from disc_metadata import local_metadata_from_cdtext
from disc_matching import MatchingPolicy, decide_release, merged_metadata
from metadata_language_rules import load_language_rules
from disc_musicbrainz import identity_from_toc, lookup_disc
from cover_preferences import read_cover_preferences


def matching_policy_from_config(metadata):
    """Share the explicit matching policy with the stand-alone diagnostic."""
    values = metadata.get('matching', {})
    if not isinstance(values, dict):
        raise ValueError('metadata.matching must be a table')
    defaults = MatchingPolicy()

    def suffixes(name, fallback):
        supplied = values.get(name, list(fallback))
        if type(supplied) is not list or any(type(s) is not str for s in supplied):
            raise ValueError(f'metadata.matching.{name} must be a list')
        return tuple(supplied)

    languages = values.get('rule_languages', [])
    if type(languages) is not list or any(type(s) is not str for s in languages):
        raise ValueError('metadata.matching.rule_languages must be a list')
    return MatchingPolicy(
        auto_match=metadata.get('auto_match', defaults.auto_match),
        confidence_threshold=values.get('confidence_threshold', defaults.confidence_threshold),
        minimum_margin=values.get('minimum_margin', defaults.minimum_margin),
        minimum_track_titles=values.get('minimum_track_titles', defaults.minimum_track_titles),
        soft_suffixes=suffixes('soft_suffixes', defaults.soft_suffixes),
        protected_suffixes=suffixes('protected_suffixes', defaults.protected_suffixes),
        language_rules=load_language_rules(tuple(languages)))


#: An offline/unavailable lookup is tried once more after this time (D-069).
RETRY_SECONDS = 120.0
_RETRY_STATUSES = ('offline', 'unavailable', 'error')


def _prefer_cdtext(local, cached):
    """CD-TEXT read now wins over cached fields (D-041); the rest comes from the cache."""
    if local.cdtext_status != 'available':
        return cached
    tracks = cached.tracks
    if len(tracks) == len(local.tracks):
        tracks = tuple(replace(old, title=new.title or old.title,
                               artist=new.artist or old.artist)
                       for new, old in zip(local.tracks, tracks))
    return replace(cached, album=local.album or cached.album,
                   artist=local.artist or cached.artist, tracks=tracks,
                   cdtext_status=local.cdtext_status)


def _cover_file(cover_cache, release_id, cover_policy):
    """(path or None, reason). Covers are optional: failures keep the titles."""
    if not release_id:
        return None, 'no_release'
    if not cover_policy.enabled:
        return None, 'off'
    try:
        path = cover_cache.get_or_download(release_id, policy=cover_policy)
    except Exception as exc:
        return None, 'failed:' + type(exc).__name__
    # None from the cache = no front image at the Cover Art Archive or download failed.
    return (path, 'ok') if path is not None else (None, 'unavailable')


def describe(snapshot):
    """One journal line per disc: how the names and the cover were found."""
    if not isinstance(snapshot, dict):
        return '[metadata] none'
    titles = sum(1 for t in snapshot.get('tracks') or () if t.get('title'))
    release = snapshot.get('release_id') or '-'
    return ('[metadata] disc={disc} match={match} cdtext={cdtext} lookup={lookup} '
            'release={release} titles={titles}/{count} cover={cover}').format(
        disc=snapshot.get('disc_id') or '-', match=snapshot.get('match_status'),
        cdtext=snapshot.get('cdtext_status'), lookup=snapshot.get('lookup_status') or '-',
        release=release, titles=titles, count=len(snapshot.get('tracks') or ()),
        cover=snapshot.get('cover_status'))


def _safe_result(toc, cdtext=None, *, cache, cover_cache, cover_policy, online, timeout,
                 policy, identify, lookup):
    identity = identify(toc)
    local = local_metadata_from_cdtext(toc, cdtext)
    # Database corruption and permissions must not hide already-read CD-TEXT.
    try:
        cached = cache.get(identity)
    except Exception:
        cached = None
    lookup_status = None
    if cached is not None:
        effective = _prefer_cdtext(local, cached.local)
        release_id = cached.release_id
        match = 'cached'
        score = cached.confidence_score
    else:
        effective, release_id, match, score = local, None, 'toc', None
        if online:
            # The remote provider is optional. Its exceptions have the same
            # retry semantics as a structured 'error' result (D-227).
            try:
                result = lookup(identity, track_count=len(toc.tracks),
                                timeout_seconds=timeout)
                lookup_status = result.status
            except Exception:
                result = None
                lookup_status = 'error'
            if result is not None and result.status in ('unique', 'ambiguous'):
                # Bad external data must not remove the local metadata either.
                try:
                    decision = decide_release(local, result, policy)
                    merged = merged_metadata(local, decision)
                except Exception:
                    lookup_status = 'error'
                else:
                    release_id = decision.selected_release_id
                    match, score = decision.status, decision.score
                    effective = merged
                    if release_id:
                        try:
                            cache.put(identity, effective, release_id=release_id,
                                      match_method=match, confidence_score=score)
                        except Exception:
                            # Metadata remains available even with read-only DB.
                            pass
    # Cover mode off suppresses even cached files in the UI.
    cover, cover_status = _cover_file(cover_cache, release_id, cover_policy)
    return {
        'disc_id': identity.disc_id,
        'album': effective.album,
        'artist': effective.artist,
        'tracks': [{'number': t.number, 'title': t.title,
                    'artist': t.artist, 'duration_seconds': t.duration_seconds}
                   for t in effective.tracks],
        'release_id': release_id,
        'match_status': match,
        'match_score': score,
        'cover_file': str(cover) if cover is not None else None,
        'cover_status': cover_status,
        'cdtext_status': local.cdtext_status,
        'lookup_status': lookup_status,
    }


class MetadataWorker:
    """One in-flight metadata job. Stale completions can never change display."""

    def __init__(self, *, cache_path=DEFAULT_DB_PATH, cover_dir=DEFAULT_COVER_DIR,
                 online=True, timeout=8, matching_policy=None,
                 ui_path=None, display_supports_cover=False, sonos_uses_cover=False,
                 identify=identity_from_toc, lookup=lookup_disc,
                 executor=None, clock=time.monotonic, retry_seconds=RETRY_SECONDS):
        self._cache = DiscCache(cache_path)
        self._cover = CoverCache(cover_dir)
        self._policy = matching_policy or MatchingPolicy()
        self._cover_policy = read_cover_preferences(ui_path).download_policy(
            display_supports_cover=display_supports_cover,
            sonos_uses_cover=sonos_uses_cover)
        self._online, self._timeout = online, timeout
        self._identify, self._lookup = identify, lookup
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='disc-metadata')
        self._owns_executor = executor is None
        self._pending = None
        self._pending_toc = None
        self._last_toc = None
        self._last_cdtext = None
        self._result = None
        self._clock = clock
        self._retry_seconds = retry_seconds
        self._retry_at = None       # one later lookup when the first was offline
        self._retried = None        # TOC whose one retry is used up

    def observe(self, toc, cdtext=None):
        """Called on the serialized player worker after a verified TOC change.

        ``cdtext`` is the reading the player took right after the TOC
        (``read_cdtext`` result); this worker never touches the drive.
        """
        if toc == self._last_toc:
            return False
        self._last_toc = toc
        self._last_cdtext = cdtext
        self._result = None
        self._retry_at = None
        if toc is None:
            self._pending, self._pending_toc = None, None
            return True
        self._submit(toc)
        return True

    def _submit(self, toc):
        self._pending_toc = toc
        self._pending = self._executor.submit(self._run, toc, self._last_cdtext)

    def _run(self, toc, cdtext=None):
        try:
            return _safe_result(
                toc, cdtext, cache=self._cache, cover_cache=self._cover,
                cover_policy=self._cover_policy, online=self._online,
                timeout=self._timeout, policy=self._policy,
                identify=self._identify, lookup=self._lookup)
        except Exception:
            # Even corrupt caches or inaccessible services must not block PLAY.
            return None

    def poll(self, toc):
        """Never waits. Consume the result only for the currently verified TOC."""
        if (self._pending is None and self._retry_at is not None
                and toc == self._last_toc and self._clock() >= self._retry_at):
            self._retry_at = None
            self._submit(toc)
            return False
        if self._pending is None or not self._pending.done():
            return False
        pending, expected = self._pending, self._pending_toc
        self._pending, self._pending_toc = None, None
        if toc != expected or toc != self._last_toc:
            return False
        value = pending.result()
        if value is None:
            return False
        if value.get('lookup_status') in _RETRY_STATUSES and self._retried != toc:
            # Only one later attempt per disc; never a retry loop.
            self._retried = toc
            self._retry_at = self._clock() + self._retry_seconds
        self._result = value
        return True

    @property
    def snapshot(self):
        return self._result.copy() if self._result is not None else None

    def close(self):
        if self._owns_executor:
            # Ongoing network work must not delay the service's shutdown.
            self._executor.shutdown(wait=False, cancel_futures=True)
