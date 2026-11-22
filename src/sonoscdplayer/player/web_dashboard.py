"""Cached, read-only dashboard data for local HTTP clients.

The normal status reply stays small for button/OLED/LED clients. Disc titles
come from the metadata snapshot already verified by the player. Room names
are resolved in a *separate* background worker so the serialized audio
controller never waits for discovery just to serve a dashboard.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import threading
import time


def safe_text(value, limit=160):
    """Bound display text without changing meaningful spelling."""
    if not isinstance(value, str):
        return None
    text = ''.join(char for char in value if char.isprintable()).strip()
    return text[:limit] or None


def discover_names(uids):
    """Best-effort names; caller must execute this *outside* player worker."""
    from soco import discover
    names = {}
    for speaker in discover(timeout=3) or ():
        try:
            uid = speaker.uid
            if uid in uids:
                name = safe_text(speaker.player_name, 80)
                if name:
                    names[uid] = name
        except Exception:
            # One unreachable speaker must not discard other room names.
            continue
    return names


def dashboard_tracks(controller):
    """Present cached titles only; never inspect the drive or load SQLite."""
    state = getattr(controller, 'state', None)
    numbers = tuple(getattr(state, 'track_numbers', ()) or ())[:99]
    metadata = getattr(controller, 'metadata_snapshot', None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    raw_tracks = metadata.get('tracks') or ()
    entries = {t['number']: t for t in raw_tracks if isinstance(t, dict)
               and type(t.get('number')) is int}
    return [
        {'number': number,
         'title': safe_text(entries.get(number, {}).get('title')),
         'artist': safe_text(entries.get(number, {}).get('artist'))}
        for number in numbers if type(number) is int and 1 <= number <= 99
    ]


class DashboardPublisher:
    """Serve cached dashboard data while Sonos PLAY may be starting.

    The caller invokes publish() on the serialized player worker; read() may
    run on the socket accept loop. The optional injected resolver is for tests.
    """

    def __init__(self, controller, selection, *, resolve=discover_names, clock=time.monotonic):
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='speaker-names')
        self._resolve = resolve
        self._clock = clock
        self._requested = ()
        self._names = {}
        self._pending = None
        self._last_attempt = float('-inf')
        self._reply = None
        self.publish(controller, selection)

    def publish(self, controller, selection):
        """Refresh cached labels without synchronous discovery or I/O."""
        selected = tuple(selection.speakers())
        # The player follows app group changes via the selection_hook. When
        # ownership is active, show that known group rather than the last idle
        # selection, but never claim to be playing in STOPPED mode.
        state = getattr(controller, 'state', None)
        mode = getattr(getattr(state, 'mode', None), 'value', None)
        playing = bool(getattr(controller, 'owned_url', None)) and mode in ('PLAYING', 'PAUSED', 'STARTING')
        uids = selected
        if playing and getattr(controller, 'uid', None):
            uids = (controller.uid,) + tuple(uid for uid in selected if uid != controller.uid)
        if uids != self._requested:
            self._requested = uids
            self._names = {}
            self._last_attempt = float('-inf')
        if self._pending is not None and self._pending[1].done():
            wanted, future = self._pending
            try:
                found = future.result()
            except Exception:
                found = {}
            if wanted == uids and isinstance(found, dict):
                self._names = {uid: safe_text(found.get(uid), 80)
                               for uid in uids if safe_text(found.get(uid), 80)}
            self._pending = None
        if uids and self._pending is None and len(self._names) < len(uids) and self._clock() - self._last_attempt >= 60:
            self._last_attempt = self._clock()
            try:
                self._pending = (uids, self._pool.submit(self._resolve, frozenset(uids)))
            except RuntimeError:
                # A name-discovery failure must not prevent CD playback.
                self._pending = None
        tracks = dashboard_tracks(controller)
        metadata = getattr(controller, 'metadata_snapshot', None)
        if not isinstance(metadata, dict):
            metadata = {}
        reply = {
            'ok': True,
            'dashboard': {
                'track_count': len(tracks),
                'tracks': tracks,
                'speaker_names': [self._names[uid] for uid in uids if uid in self._names],
                'speaker_count': len(uids),
                'target_rev': getattr(controller, 'target_rev', 0),
                'playing_on_selection': playing,
                'album': metadata.get('album'),
                'artist': metadata.get('artist'),
                'edit_token': metadata.get('edit_token'),
                'manual_fields': metadata.get('manual_fields', []),
                'metadata_source': metadata.get('metadata_source'),
                'edit_error': metadata.get('edit_error'),
                'edit_seq': metadata.get('edit_seq', 0),
                'release_status': metadata.get('release_status', 'idle'),
                'release_candidates': metadata.get('release_candidates', []),
                'release_total': metadata.get('release_total', 0),
                'release_error': metadata.get('release_error'),
                'release_seq': metadata.get('release_seq', 0),
                'release_selected_id': metadata.get('release_selected_id'),
            },
        }
        with self._lock:
            self._reply = reply

    def read(self):
        """Read only memory: safe even when another command is in flight."""
        with self._lock:
            return copy.deepcopy(self._reply)

    def close(self):
        """Do not wait for unfinished Sonos discovery on shutdown."""
        self._pool.shutdown(wait=False, cancel_futures=True)
