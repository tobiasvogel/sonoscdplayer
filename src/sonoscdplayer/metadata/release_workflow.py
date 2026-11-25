"""WEB-03b: asynchronous release search and explicit selection for MetadataWorker.

The mixin deliberately has no own executor and no optical drive access. Its
methods are called only on the serialized player worker; all remote/SQLite
work uses the existing MetadataWorker executor.
"""
from __future__ import annotations

from sonoscdplayer.metadata.local_metadata import local_metadata_from_cdtext
from sonoscdplayer.metadata.manual_edits import disc_token
from sonoscdplayer.metadata.release_selection import (
    confirm_release, release_detail, release_summaries, valid_release_id,
)


class ReleaseWorkflow:
    def _init_releases(self):
        self._release_lookup = None
        self._release_selection = None
        self._release_items = {}
        self._release_info = {'status': 'idle', 'candidates': [], 'total': 0,
                              'error': None, 'seq': 0}

    def _reset_releases(self):
        # A superseded future may finish, but its completion is never adopted.
        self._init_releases()

    def _release_identity(self, token):
        if (type(token) is not str or len(token) != 32 or
                any(c not in '0123456789abcdef' for c in token)):
            raise ValueError('invalid disc token')
        if self._last_toc is None or self._result is None:
            raise ValueError('no identified disc ready')
        identity = self._identify(self._last_toc)
        if disc_token(identity) != token or self._result.get('edit_token') != token:
            raise ValueError('disc changed; reload metadata')
        return identity

    def enqueue_release_search(self, *, token):
        identity = self._release_identity(token)
        if self._release_selection is not None:
            raise RuntimeError('release selection still pending')
        if self._release_lookup is not None:
            raise RuntimeError('release search still pending')
        # Provider I/O is never performed by the player control thread.
        future = self._executor.submit(self._lookup, identity,
                                       track_count=len(self._last_toc.tracks),
                                       timeout_seconds=self._timeout)
        self._release_lookup = (token, future)
        self._release_items = {}
        self._release_info.update(status='searching', candidates=[], total=0,
                                  error=None)
        self._release_info['seq'] += 1

    def _can_keep_track_edits(self, candidate):
        protected = self._result.get('manual_fields') or ()
        track_keys = [key for key in protected if ':' in key]
        if not track_keys:
            return True
        if len(candidate.track_titles) != len(self._last_toc.tracks):
            return False
        automatic = self._result.get('_automatic') or self._result
        originals = {row.get('number'): row.get('title')
                     for row in automatic.get('tracks', ()) if isinstance(row, dict)}
        for key in track_keys:
            try:
                number = int(key.split(':', 1)[0])
            except (TypeError, ValueError):
                return False
            if not 1 <= number <= len(candidate.track_titles):
                return False
            old = originals.get(number)
            new = candidate.track_titles[number-1]
            if not (isinstance(old, str) and isinstance(new, str) and
                    ' '.join(old.casefold().split()) == ' '.join(new.casefold().split())):
                return False
        return True

    def release_detail_for(self, *, token, release_id):
        self._release_identity(token)
        if not valid_release_id(release_id):
            raise ValueError('invalid release ID')
        candidate = self._release_items.get(release_id)
        if candidate is None:
            raise ValueError('release no longer in candidate list')
        detail = release_detail(candidate)
        detail['can_keep_track_edits'] = self._can_keep_track_edits(candidate)
        return detail

    def enqueue_release_selection(self, *, token, release_id, keep_manual):
        identity = self._release_identity(token)
        if type(keep_manual) is not bool:
            raise ValueError('choose whether to keep manual corrections')
        if not valid_release_id(release_id):
            raise ValueError('invalid release ID')
        candidate = self._release_items.get(release_id)
        if candidate is None:
            raise ValueError('release no longer in candidate list')
        if self._release_selection is not None or self._edit_jobs:
            raise RuntimeError('metadata write already pending')
        protected = self._result.get('manual_fields') or ()
        # D-090/091: a differing medium cannot safely inherit track edits.
        if keep_manual and not self._can_keep_track_edits(candidate):
            raise ValueError('cannot safely map track edits to this release; discard or cancel')
        toc = self._last_toc
        generation = self._disc_generation
        local = local_metadata_from_cdtext(toc, self._last_cdtext)
        def work():
            return confirm_release(
                self._cache, identity, local, candidate, keep_manual=keep_manual,
                current=lambda: (self._disc_generation == generation and
                                 self._last_toc == toc))
        future = self._executor.submit(work)
        self._release_selection = (token, future)
        self._release_info.update(status='saving', error=None)
        self._release_info['seq'] += 1

    def _poll_releases(self, toc):
        changed = False
        if self._release_lookup is not None:
            token, future = self._release_lookup
            if future.done():
                self._release_lookup = None
                if self._result is not None and self._result.get('edit_token') == token:
                    try:
                        answer = future.result()
                        local = local_metadata_from_cdtext(toc, self._last_cdtext)
                        candidates = (answer.releases if answer.status in
                                      ('unique', 'ambiguous') else ())
                        summaries = release_summaries(candidates, local, self._policy)
                        self._release_items = {
                            item.release_id: item for item in candidates
                            if valid_release_id(item.release_id)
                        }
                        self._release_info.update(
                            status='ready' if summaries else 'not_found',
                            candidates=summaries, total=len(self._release_items),
                            error=None if answer.status not in ('offline', 'unavailable', 'error')
                            else 'metadata.error.musicbrainz_unavailable')
                        if answer.status in ('offline', 'unavailable', 'error'):
                            self._release_info['status'] = 'error'
                    except Exception:
                        self._release_items = {}
                        self._release_info.update(status='error', candidates=[], total=0,
                                                  error='metadata.error.release_search_failed')
                    self._release_info['seq'] += 1
                    changed = True
        if self._release_selection is not None:
            token, future = self._release_selection
            if future.done():
                self._release_selection = None
                if self._result is not None and self._result.get('edit_token') == token:
                    try:
                        future.result()
                    except Exception:
                        self._release_info.update(status='ready', error='metadata.error.release_save_failed')
                    else:
                        self._release_info.update(status='ready', error=None)
                        self._result = None
                        self._pending = self._pending_toc = None
                        self._retry_at = None
                        self._submit(toc)  # read the newly confirmed cache off-thread
                    self._release_info['seq'] += 1
                    changed = True
        return changed

    def _release_snapshot(self):
        if self._result is None:
            return None
        result = self._result.copy()
        result.update(release_status=self._release_info['status'],
                      release_candidates=[dict(item) for item in self._release_info['candidates']],
                      release_total=self._release_info['total'],
                      release_error=self._release_info['error'],
                      release_seq=self._release_info['seq'],
                      release_selected_id=self._result.get('release_id'))
        return result
