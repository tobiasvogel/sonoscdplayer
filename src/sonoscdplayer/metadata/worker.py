"""Fetch metadata for the inserted CD in the background.

Order: CD-TEXT (already read with the TOC), the SQLite cache, MusicBrainz,
then the cover.

No optical-drive reads or Sonos operations. A single background worker handles
SQLite, MusicBrainz and optional cover files. The player worker alone applies
completed results, and only while the identical TOC remains selected.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from sonoscdplayer.metadata.cache import DiscCache, DEFAULT_DB_PATH
from sonoscdplayer.metadata.cover_store import CoverBudgetFull, CoverStore, DEFAULT_COVER_DIR
import time

from sonoscdplayer.metadata.local_metadata import local_metadata_from_cdtext
from sonoscdplayer.metadata.release_matching import MatchingPolicy, decide_release, merged_metadata
from sonoscdplayer.metadata.language_rules import load_language_rules
from sonoscdplayer.metadata.musicbrainz import identity_from_toc, lookup_disc
from sonoscdplayer.metadata.cover_preferences import read_cover_preferences
from sonoscdplayer.metadata.gnudb import query_disc as query_gnudb, read_entry as read_gnudb
from sonoscdplayer.metadata.gnudb_fallback import resolve_fallback
from sonoscdplayer.metadata.manual_edits import ManualEdits, apply_edits, disc_token, validate_edit
from sonoscdplayer.metadata.release_workflow import ReleaseWorkflow
from sonoscdplayer.metadata.manual_cover import ManualCoverWorkflow, DEFAULT_STAGING
from sonoscdplayer.metadata.library_index import LibraryWorkflow
from sonoscdplayer.metadata.library_manage import LibraryManageWorkflow
from sonoscdplayer.metadata.library_maintenance import LibraryMaintenanceWorkflow


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


#: An offline/unavailable lookup is tried again after these delays (s),
#: then no more for this disc.
RETRY_DELAYS = (120.0, 600.0, 1800.0)
_RETRY_STATUSES = ('offline', 'unavailable', 'error')


def _prefer_cdtext(local, cached):
    """CD-TEXT read now wins over cached fields; the rest comes from the cache."""
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
    except CoverBudgetFull:
        return None, 'budget_full'
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
    return ('[metadata] disc={disc} match={match} source={source} cdtext={cdtext} lookup={lookup} '
            'release={release} titles={titles}/{count} cover={cover}{error}').format(
        disc=snapshot.get('disc_id') or '-', match=snapshot.get('match_status'),
        source=snapshot.get('metadata_source') or 'unknown',
        cdtext=snapshot.get('cdtext_status'), lookup=snapshot.get('lookup_status') or '-',
        error=f" ({snapshot['lookup_error']})" if snapshot.get('lookup_error') else '',
        release=release, titles=titles, count=len(snapshot.get('tracks') or ()),
        cover=snapshot.get('cover_status'))


def _compatible_cached_gnudb(local, cached):
    """Refuse stored CDDB names that conflict with current CD-TEXT."""
    def same(a, b):
        return not a or ' '.join(a.casefold().split()) == ' '.join((b or '').casefold().split())
    return (len(local.tracks) == len(cached.tracks) and
            same(local.album, cached.album) and same(local.artist, cached.artist) and
            all(same(left.title, right.title) for left, right in
                zip(local.tracks, cached.tracks)))


def _safe_result(toc, cdtext=None, *, cache, cover_cache, cover_policy, online, timeout,
                 policy, identify, lookup, gnudb_enabled=False, gnudb_timeout=5,
                 gnudb_query=query_gnudb, gnudb_read=read_gnudb):
    identity = identify(toc)
    local = local_metadata_from_cdtext(toc, cdtext)
    # Database corruption and permissions must not hide already-read CD-TEXT.
    try:
        cached = cache.get(identity)
    except Exception:
        cached = None
    # A CDDB record has no MusicBrainz release ID. It remains a fallback,
    # never a reason to skip MusicBrainz on the next disc insertion.
    if cached is not None and cached.match_method == 'gnudb' and not gnudb_enabled:
        cached = None
    cached_gnudb = cached if cached is not None and cached.match_method == 'gnudb' else None
    if cached_gnudb is not None and not _compatible_cached_gnudb(local, cached_gnudb.local):
        cached_gnudb = None
        cached = None
    lookup_status = None
    lookup_error = None
    alternatives = ()
    album_confirmed = True          # False: majority names without an album name
    if cached is not None and cached_gnudb is None:
        effective = _prefer_cdtext(local, cached.local)
        release_id = cached.release_id
        match = 'manual' if cached.confirmed_by_user else 'cached'
        score = cached.confidence_score
    else:
        effective, release_id, match, score = local, None, 'toc', None
        if cached_gnudb is not None:
            effective = _prefer_cdtext(local, cached_gnudb.local)
            match = 'gnudb'
        if online:
            # The remote provider is optional. Its exceptions have the same
            # retry semantics as a structured 'error' result.
            try:
                result = lookup(identity, track_count=len(toc.tracks),
                                timeout_seconds=timeout)
                lookup_status = result.status
                lookup_error = result.error
            except Exception as exc:
                result = None
                lookup_status = 'error'
                lookup_error = type(exc).__name__
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
                    same_album = (decision.chosen.title if decision.chosen is not None else None)
                    album_confirmed = decision.chosen is None or bool(same_album)
                    alternatives = tuple(
                        item.release.release_id for item in decision.candidates
                        if item.release.release_id != release_id
                        and (same_album is None or policy.language_rules.normalize(
                            item.release.title) == policy.language_rules.normalize(same_album)))
                    if release_id:
                        try:
                            cache.put(identity, effective, release_id=release_id,
                                      match_method=match, confidence_score=score)
                        except Exception:
                            # Metadata remains available even with read-only DB.
                            pass
        if (online and gnudb_enabled and cached_gnudb is None and release_id is None and
                lookup_status in ('not_found', 'unavailable', 'offline', 'error')):
            # Full TOC validation and CD-TEXT priority prevent CDDB collisions
            # from silently replacing known metadata. CDDB has no MB release ID.
            fallback, _fallback_status = resolve_fallback(
                toc, local, timeout_seconds=gnudb_timeout,
                query=gnudb_query, read=gnudb_read)
            if fallback is not None:
                effective, match, score = fallback, 'gnudb', None
                try:
                    cache.put(identity, effective, match_method='gnudb')
                except Exception:
                    # A read-only or corrupt cache must not block metadata.
                    pass
    # Cover mode off suppresses even cached files in the UI.
    cover_release = release_id
    if match == 'majority' and not album_confirmed:
        cover_release = None        # no edition is confirmed by an album name: no cover
    cover, cover_status = _cover_file(cover_cache, cover_release, cover_policy)
    if match in ('consensus', 'majority') and cover_release and cover_status == 'unavailable':
        # Same names (on all or most editions): take the cover of the first
        # such edition that has one.
        for other in alternatives:
            path, status = _cover_file(cover_cache, other, cover_policy)
            if path is not None:
                cover, cover_status, release_id = path, status, other
                try:
                    cache.put(identity, effective, release_id=release_id,
                              match_method=match, confidence_score=score)
                except Exception:
                    pass
                break
    snapshot = {
        'disc_id': identity.disc_id,
        'album': effective.album,
        'artist': effective.artist,
        'tracks': [{'number': t.number, 'title': t.title,
                    'artist': t.artist, 'duration_seconds': t.duration_seconds}
                   for t in effective.tracks],
        'release_id': release_id,
        'match_status': match,
        'metadata_source': ('gnudb' if match == 'gnudb' else
                            'musicbrainz' if release_id is not None else
                            'cdtext' if local.cdtext_status == 'available' else 'toc'),
        'match_score': score,
        'cover_file': str(cover) if cover is not None else None,
        'cover_status': cover_status,
        'cdtext_status': local.cdtext_status,
        'lookup_status': lookup_status,
        'lookup_error': lookup_error,
        'edit_token': disc_token(identity),
    }
    try:
        edits = ManualEdits(cache.path).get(identity)
    except Exception:
        # Manual cache corruption must not hide verified CD-TEXT or music data.
        edits = {}
    return apply_edits(snapshot, edits)


class MetadataWorker(LibraryMaintenanceWorkflow, LibraryManageWorkflow, LibraryWorkflow, ManualCoverWorkflow, ReleaseWorkflow):
    """One in-flight metadata job. Stale completions can never change display."""

    def __init__(self, *, cache_path=DEFAULT_DB_PATH, cover_dir=DEFAULT_COVER_DIR,
                 online=True, timeout=8, matching_policy=None,
                 ui_path=None, display_supports_cover=False, sonos_uses_cover=False,
                 cover_state_dir=None,
                 identify=identity_from_toc, lookup=lookup_disc,
                 executor=None, clock=time.monotonic, retry_delays=RETRY_DELAYS,
                 gnudb_enabled=False, gnudb_timeout=5,
                 gnudb_query=query_gnudb, gnudb_read=read_gnudb,
                 cover_staging_dir=DEFAULT_STAGING):
        self._cache = DiscCache(cache_path)
        self._manual = ManualEdits(cache_path)
        self._edit_jobs = []
        self._init_releases()
        self._cover = CoverStore(cover_dir)
        self._init_manual_covers(cover_dir, ui_path, cover_staging_dir)
        self._init_library(cache_path, cover_dir)
        self._init_library_manage(cache_path)
        self._init_library_maintenance(cache_path, cover_dir, ui_path)
        self._policy = matching_policy or MatchingPolicy()
        self._cover_policy = read_cover_preferences(
            ui_path, state_dir=cover_state_dir).download_policy(
            display_supports_cover=display_supports_cover,
            sonos_uses_cover=sonos_uses_cover)
        if type(gnudb_enabled) is not bool or type(gnudb_timeout) is not int or not 1 <= gnudb_timeout <= 30:
            raise ValueError('invalid GnuDB fallback settings')
        self._online, self._timeout = online, timeout
        self._identify, self._lookup = identify, lookup
        self._gnudb_enabled, self._gnudb_timeout = gnudb_enabled, gnudb_timeout
        self._gnudb_query, self._gnudb_read = gnudb_query, gnudb_read
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='disc-metadata')
        self._owns_executor = executor is None
        self._pending = None
        self._pending_toc = None
        self._last_toc = None
        self._last_cdtext = None
        self._result = None
        self._clock = clock
        self._retry_delays = tuple(retry_delays)
        self._retry_at = None       # next lookup when the last was offline/unavailable
        self._retries = 0           # retries already scheduled for the current disc

    def observe(self, toc, cdtext=None):
        """Called on the serialized player worker after a verified TOC change.

        ``cdtext`` is the reading the player took right after the TOC
        (``read_cdtext`` result); this worker never touches the drive.
        """
        if toc == self._last_toc:
            return False
        self._last_toc = toc
        self._reset_manual_covers()
        self._reset_releases()
        self._last_cdtext = cdtext
        self._result = None
        self._retry_at = None
        self._retries = 0
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
            value = _safe_result(
                toc, cdtext, cache=self._cache, cover_cache=self._cover,
                cover_policy=self._cover_policy, online=self._online,
                timeout=self._timeout, policy=self._policy,
                identify=self._identify, lookup=self._lookup,
                gnudb_enabled=self._gnudb_enabled, gnudb_timeout=self._gnudb_timeout,
                gnudb_query=self._gnudb_query, gnudb_read=self._gnudb_read)
            if value is not None:
                path = self._manual_covers.find(self._identify(toc))
                value['_manual_cover_file'] = str(path) if path is not None else None
            return value
        except Exception:
            # Even corrupt caches or inaccessible services must not block PLAY.
            return None

    def enqueue_edit(self, *, token, field, track=None, value=None):
        """Queue a disc-qualified edit without blocking player or audio control."""
        field, track, value = validate_edit(field, track, value)
        if not isinstance(token, str) or len(token) != 32:
            raise ValueError('invalid disc token')
        if self._last_toc is None or self._result is None:
            raise ValueError('no identified disc ready for editing')
        identity = self._identify(self._last_toc)  # cached TOC only; no drive I/O
        if disc_token(identity) != token or self._result.get('edit_token') != token:
            raise ValueError('disc changed; reload metadata')
        if track is not None and track not in {entry['number'] for entry in self._result.get('tracks', ())}:
            raise ValueError('track not on this disc')
        if len(self._edit_jobs) >= 8:
            raise RuntimeError('too many pending metadata edits')
        future = self._executor.submit(self._manual.set, identity,
                                       field=field, track=track, value=value)
        self._edit_jobs.append((token, future))

    def _consume_edits(self):
        changed = False
        pending = []
        for token, future in self._edit_jobs:
            if not future.done():
                pending.append((token, future))
                continue
            try:
                fields = future.result()
            except Exception as exc:
                print(f'[metadata] manual edit failed: {type(exc).__name__}', flush=True)
                if self._result is not None and self._result.get('edit_token') == token:
                    self._result['edit_error'] = 'metadata.error.edit_save_failed'
                    self._result['edit_seq'] = self._result.get('edit_seq', 0) + 1
                    changed = True
                continue
            if self._result is not None and self._result.get('edit_token') == token:
                self._result = apply_edits(self._result, fields)
                self._result['edit_seq'] = self._result.get('edit_seq', 0) + 1
                self._result['edit_error'] = None
                changed = True
        self._edit_jobs = pending
        return changed

    def poll(self, toc):
        """Never waits. Consume the result only for the currently verified TOC."""
        self._poll_library()
        self._poll_library_manage()
        self._poll_library_maintenance()
        covered = self._poll_manual_covers()
        edited = self._consume_edits() or covered
        edited = self._poll_releases(toc) or edited
        if (self._pending is None and self._retry_at is not None
                and toc == self._last_toc and self._clock() >= self._retry_at):
            self._retry_at = None
            self._submit(toc)
            return edited
        if self._pending is None or not self._pending.done():
            return edited
        pending, expected = self._pending, self._pending_toc
        self._pending, self._pending_toc = None, None
        if toc != expected or toc != self._last_toc:
            return edited
        value = pending.result()
        if value is None:
            return edited
        if (value.get('lookup_status') in _RETRY_STATUSES
                and self._retries < len(self._retry_delays)):
            # A few later attempts per disc, further apart; never a loop.
            self._retry_at = self._clock() + self._retry_delays[self._retries]
            self._retries += 1
        if not covered:
            self._cover_path = value.get('_manual_cover_file')
        value.pop('_manual_cover_file', None)
        self._result = value
        return True

    def reset_current(self):
        """Forget the stored entry of the inserted CD and look it up again.

        Only this disc's metadata entry is removed; covers stay (they belong
        to a release and may be shared). CD-TEXT read at insertion is kept.
        """
        toc = self._last_toc
        if toc is None:
            raise ValueError('no disc inserted')
        if self._edit_jobs:
            raise RuntimeError('manual edits still pending')
        if self._release_selection is not None:
            raise RuntimeError('release selection still pending')
        if self._cover_pending is not None:
            raise RuntimeError('cover operation still pending')
        if self._maintenance_job is not None or self._library_manage_job is not None:
            raise RuntimeError('library write still pending')
        self._reset_releases()
        identity = self._identify(toc)
        self._manual.delete(identity)
        self._cache.delete(identity)
        self._result = None
        self._retry_at, self._retries = None, 0
        self._submit(toc)
        return True

    @property
    def snapshot(self):
        """Current metadata state for the status reply."""
        return self._manual_cover_snapshot(self._release_snapshot())

    def close(self):
        """Stop the background worker."""
        if self._owns_executor:
            # Ongoing network work must not delay the service's shutdown.
            self._executor.shutdown(wait=False, cancel_futures=True)
