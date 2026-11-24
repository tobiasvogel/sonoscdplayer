"""Minimal web client with no external CSS, JS or Python dependencies.

The web service never opens the drive or talks to Sonos. All active commands
are forwarded to the player's private Unix socket. Metadata editing and
configuration overlays are separate follow-up slices, not simulated writes.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit, parse_qs

from .player_client import PlayerClient, PlayerUnavailable, command_payload, metadata_edit_payload, release_payload, cover_payload, library_payload, library_manage_payload, library_maintenance_payload
from .cover_upload import stage_upload, discard_upload
from sonoscdplayer.metadata.manual_cover import MAX_UPLOAD_BYTES
from sonoscdplayer.metadata.cover_preferences import read_cover_preferences
from .state import WebPreferences
from .dashboard_projection import public_dashboard, public_release_detail
from .library_projection import public_library
from .settings_read import settings_snapshot, SettingsUnavailable, _document, MAX_CONFIG_BYTES
from sonoscdplayer.system import config_paths
from sonoscdplayer.ui.language_state import UiState, LanguageConflict
from sonoscdplayer.ui.cover_state import save_cover_mode, CoverConflict
from sonoscdplayer.hardware.inputs.event_engine import load_config as load_input_config
from sonoscdplayer.hardware.inputs.input_settings_write import (
    update_input_settings, InputSettingsConflict,
)
from sonoscdplayer.player.playback_settings import (FIELDS as PLAYBACK_FIELDS, valid as playback_valid,
                                                    update as update_playback, SettingsConflict)
from .i18n import (web_catalog, web_message, metadata_error_message,
                   player_rejection_message)


STATIC = Path(__file__).resolve().parent / 'static'
MAX_BODY = 4096


def public_status(response: dict) -> dict:
    """Publish state without private UIDs, absolute cover paths or raw logs."""
    state = response.get('state', {})
    if not isinstance(state, dict):
        state = {}
    # A defensive, explicit projection avoids exposing new private status keys.
    player = state.get('player') or {}
    disc = state.get('disc') or {}
    metadata = state.get('metadata') or {}
    progress = state.get('progress') or {}
    sonos = state.get('sonos') or {}
    rejection = state.get('last_rejection') or {}
    if not isinstance(player, dict): player = {}
    if not isinstance(disc, dict): disc = {}
    if not isinstance(metadata, dict): metadata = {}
    if not isinstance(progress, dict): progress = {}
    if not isinstance(sonos, dict): sonos = {}
    if not isinstance(rejection, dict): rejection = {}
    reason = str(rejection.get('reason') or '')
    reason = re.sub(r'RINCON_[A-Za-z0-9]+', '<speaker>', reason)
    reason = re.sub(r'(?<![\w])(?:\d{1,3}\.){3}\d{1,3}(?![\w])', '<address>', reason)
    reason = re.sub(r'/(?:home|Users)/[^\s,;]+', '<path>', reason)
    return {
        'ok': bool(response.get('ok')),
        'player': {'mode': player.get('mode'), 'track': player.get('track'),
                   'track_count': player.get('track_count'),
                   'error': 'Player error' if player.get('error') else None},
        'disc': {k: disc.get(k) for k in ('mode', 'track_count')},
        'metadata': {k: metadata.get(k) for k in ('album', 'artist', 'track_title',
                     'track_artist', 'match_status', 'lookup_status',
                     'cover_manual', 'cover_seq', 'cover_error')},
        'has_cover': bool(metadata.get('cover_file')),
        'progress': {k: progress.get(k) for k in ('elapsed_seconds', 'duration_seconds',
                     'position', 'duration', 'elapsed', 'total')},
        'sonos': {k: sonos.get(k) for k in ('transport', 'owned', 'group_cleanup_pending')},
        'target_rev': state.get('target_rev'),
        'last_rejection': {'seq': rejection.get('seq'), 'reason': reason[:160]},
    }


def make_handler(player: PlayerClient, preferences: WebPreferences):
    """Build an HTTP handler that can be exercised against a fake player."""
    settings_write_lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        server_version = 'SonosCDPlayerWeb'
        sys_version = ''

        def log_message(self, _format, *_args):
            # No URLs, speaker names, user input or secrets in the journal.
            pass

        def _json(self, code, payload):
            # Public errors are locale keys, resolved from the display catalogs.
            # Internal exception strings and filesystem details are never sent.
            if isinstance(payload, dict) and isinstance(payload.get('error'), str) and \
                    payload['error'].startswith('web.api.'):
                payload = {**payload, 'error': web_message(preferences.path.parent, payload['error'])}
            raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _file(self, data: bytes, kind: str):
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/':
                return self._file((STATIC / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            if path == '/app.js':
                return self._file((STATIC / 'app.js').read_bytes(), 'text/javascript; charset=utf-8')
            if path == '/api/i18n':
                return self._json(200, web_catalog(preferences.path.parent))
            if path == '/api/settings':
                try:
                    return self._json(200, settings_snapshot(state_root=preferences.path.parent))
                except (OSError, SettingsUnavailable):
                    return self._json(503, {'ok': False, 'error': 'web.api.settings_unavailable'})
            if path == '/api/settings/runtime':
                # A dedicated RPC reads only the in-memory values loaded on startup.
                try:
                    reply = player.request({'command': 'settings-status'}, timeout=2)
                except (PlayerUnavailable, ValueError):
                    reply = None
                values = reply.get('settings') if isinstance(reply, dict) and reply.get('ok') is True else None
                if type(values) is not dict or set(values) != set(PLAYBACK_FIELDS) or any(
                        not playback_valid(key, value) for key, value in values.items()):
                    values = None
                return self._json(200, {'ok': True, 'values': values})
            if path == '/api/theme':
                return self._json(200, {'theme': preferences.read()})
            if path == '/api/status':
                try:
                    reply = public_status(player.request({'command': 'status'}, timeout=3))
                    if reply['player']['error']:
                        reply['player']['error'] = web_message(preferences.path.parent, 'web.api.player_error')
                    reply['metadata']['cover_error'] = metadata_error_message(
                        preferences.path.parent, reply['metadata'].get('cover_error'))
                    reply['last_rejection']['reason'] = player_rejection_message(
                        preferences.path.parent, reply['last_rejection'].get('reason'))
                    return self._json(200, reply)
                except PlayerUnavailable:
                    return self._json(503, {'ok': False, 'error': 'web.api.player_unavailable'})
            if path == '/api/dashboard':
                try:
                    info = public_dashboard(player.request({'command': 'dashboard'}, timeout=3))
                    for key in ('edit_error', 'release_error'):
                        info[key] = metadata_error_message(preferences.path.parent, info.get(key))
                    return self._json(200, info)
                except (PlayerUnavailable, ValueError):
                    return self._json(503, {'ok': False, 'error': 'web.api.dashboard_unavailable'})
            if path == '/api/library':
                try:
                    info = public_library(player.request({'command': 'library'}, timeout=3))
                    library = info['library']
                    library['error'] = metadata_error_message(
                        preferences.path.parent, library.get('error'))
                    for section in ('manage', 'maintenance'):
                        data = library.get(section)
                        if isinstance(data, dict):
                            data['error'] = metadata_error_message(
                                preferences.path.parent, data.get('error'))
                    return self._json(200, info)
                except (PlayerUnavailable, ValueError):
                    return self._json(503, {'ok': False, 'error': 'web.api.library_unavailable'})
            if path == '/api/targets':
                try:
                    result = player.request({'command': 'get-target'}, timeout=8)
                    # The UID is required to select the requested speaker, but
                    # never rendered into the display's diagnostic/error text.
                    return self._json(200 if result.get('ok') else 409, {
                        'ok': bool(result.get('ok')),
                        'target_selection': {
                            'selected': bool((result.get('target_selection') or {}).get('selected')),
                            'speakers_count': len((result.get('target_selection') or {}).get('speakers') or []),
                        },
                        'error': 'web.api.target_unavailable' if not result.get('ok') else None,
                    })
                except PlayerUnavailable:
                    return self._json(503, {'ok': False, 'error': 'web.api.player_unavailable'})
            if path == '/api/release-detail':
                try:
                    query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
                    if set(query) != {'token', 'release_id'} or any(len(v) != 1 for v in query.values()):
                        raise ValueError('invalid release detail query')
                    result = player.request(release_payload({key: value[0] for key, value in query.items()},
                                                            'detail'), timeout=5)
                    if not result.get('ok'):
                        return self._json(409, {'ok': False, 'error': 'web.api.release_unavailable'})
                    return self._json(200, public_release_detail(result.get('release_detail')))
                except ValueError:
                    return self._json(400, {'ok': False, 'error': 'web.api.invalid_release_query'})
                except PlayerUnavailable:
                    return self._json(503, {'ok': False, 'error': 'web.api.player_unavailable'})
            if path == '/api/cover':
                # Only the cover supplied by the player for the inserted CD.
                # Never accept an arbitrary filesystem path from HTTP requests.
                try:
                    state = (player.request({'command': 'status'}, timeout=3).get('state') or {})
                    cover = (state.get('metadata') or {}).get('cover_file')
                    from sonoscdplayer.metadata.cover_store import DEFAULT_COVER_DIR
                    root = Path(DEFAULT_COVER_DIR).resolve()
                    candidate = Path(cover).resolve() if isinstance(cover, str) else None
                    if (candidate is None or root not in candidate.parents or
                            candidate.suffix.lower() not in ('.jpeg', '.jpg', '.png') or
                            not candidate.is_file() or candidate.stat().st_size > 6 * 1024 * 1024):
                        return self._json(404, {'error': 'web.api.cover_unavailable'})
                    kind = 'image/png' if candidate.suffix.lower() == '.png' else 'image/jpeg'
                    return self._file(candidate.read_bytes(), kind)
                except (PlayerUnavailable, OSError, ValueError):
                    return self._json(404, {'error': 'web.api.cover_unavailable'})
            self._json(404, {'error': 'web.api.not_found'})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in ('/api/command', '/api/theme', '/api/metadata',
                            '/api/releases/search', '/api/releases/select',
                            '/api/cover/upload', '/api/cover/remove',
                            '/api/library/query', '/api/library/detail',
                            '/api/library/manage', '/api/library/maintenance',
                            '/api/settings/playback', '/api/settings/language',
                            '/api/settings/cover', '/api/settings/input'):
                return self._json(404, {'error': 'web.api.not_found'})
            origin = self.headers.get('Origin')
            if origin:
                url = urlsplit(origin)
                if url.scheme not in ('http', 'https') or url.netloc != self.headers.get('Host'):
                    return self._json(403, {'error': 'web.api.cross_origin'})
            if path == '/api/cover/upload':
                if self.headers.get_content_type() != 'application/octet-stream':
                    return self._json(415, {'ok': False, 'error': 'web.api.image_required'})
                token = self.headers.get('X-Disc-Token', '')
                try:
                    size = int(self.headers.get('Content-Length', '-1'))
                except ValueError:
                    size = -1
                if not 0 < size <= MAX_UPLOAD_BYTES:
                    return self._json(413, {'ok': False, 'error': 'web.api.image_size'})
                upload_id = None
                accepted = False
                try:
                    # Validate identity before writing even temporary bytes.
                    cover_payload({'token': token, 'upload_id': '0' * 32}, 'upload')
                    self.connection.settimeout(12)
                    upload_id = stage_upload(self.rfile, size, preferences.path.parent)
                    result = player.request(cover_payload({'token': token, 'upload_id': upload_id}, 'upload'), timeout=35)
                    accepted = bool(result.get('ok'))
                    if not accepted:
                        return self._json(409, {'ok': False, 'error': 'web.api.cover_rejected'})
                    return self._json(202, {'ok': True, 'queued': True})
                except (ValueError, OSError, TimeoutError):
                    return self._json(400, {'ok': False, 'error': 'web.api.invalid_image'})
                except PlayerUnavailable:
                    return self._json(503, {'ok': False, 'error': 'web.api.outcome_unknown'})
                finally:
                    if upload_id and not accepted:
                        discard_upload(preferences.path.parent, upload_id)
            if self.headers.get_content_type() != 'application/json':
                return self._json(415, {'error': 'web.api.json_required'})
            try:
                size = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= size <= MAX_BODY:
                    raise ValueError('invalid content length')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('JSON object required')
                if path == '/api/settings/playback':
                    with settings_write_lock:
                        config = _document(config_paths.player_config(),
                                           limit=MAX_CONFIG_BYTES, required=True)
                        revision = update_playback(config, preferences.path.parent, data)
                    return self._json(200, {'ok': True, 'revision': revision})
                if path == '/api/settings/language':
                    if set(data) != {'language', 'revision'}:
                        raise ValueError('invalid language settings fields')
                    revision = UiState(preferences.path.parent, log=lambda *_: None).save_if_revision(
                        data['language'], data['revision'])
                    return self._json(200, {'ok': True, 'revision': revision})
                if path == '/api/settings/cover':
                    if set(data) != {'mode', 'revision'}:
                        raise ValueError('invalid cover settings fields')
                    with settings_write_lock:
                        # Never stage an override over an invalid base configuration.
                        read_cover_preferences(config_paths.ui_config())
                        revision = save_cover_mode(preferences.path.parent, data['mode'], data['revision'])
                    return self._json(200, {'ok': True, 'revision': revision})
                if path == '/api/settings/input':
                    with settings_write_lock:
                        # Validate the complete installed wiring with the production parser.
                        base = load_input_config(config_paths.interface_config())
                        revision = update_input_settings(preferences.path.parent, base, data)
                    return self._json(200, {'ok': True, 'revision': revision})
                if path == '/api/theme':
                    if set(data) != {'theme'}:
                        raise ValueError('theme is required')
                    preferences.set_theme(data['theme'])
                    return self._json(200, {'ok': True, 'theme': preferences.read()})
                if path == '/api/metadata':
                    request = metadata_edit_payload(data)
                elif path == '/api/library/manage':
                    request = library_manage_payload(data)
                elif path == '/api/library/maintenance':
                    request = library_maintenance_payload(data)
                elif path.startswith('/api/library/'):
                    request = library_payload(data, path.rsplit('/', 1)[1])
                elif path == '/api/cover/remove':
                    request = cover_payload(data, 'remove')
                elif path.startswith('/api/releases/'):
                    request = release_payload(data, path.rsplit('/', 1)[1])
                else:
                    request = command_payload(data)
            except (SettingsConflict, LanguageConflict, CoverConflict, InputSettingsConflict):
                return self._json(409, {'ok': False, 'error': 'web.api.settings_conflict'})
            except (OSError, SettingsUnavailable):
                return self._json(503, {'ok': False, 'error': 'web.api.setting_unsaved'})
            except (ValueError, TypeError):
                return self._json(400, {'ok': False, 'error': 'web.api.invalid_input'})
            try:
                result = player.request(request, timeout=35)
            except PlayerUnavailable:
                return self._json(503, {'ok': False, 'error': 'web.api.outcome_no_retry'})
            # Hide raw player diagnostics containing installation-specific details.
            if not result.get('ok'):
                return self._json(409, {'ok': False, 'error': 'web.api.command_rejected'})
            if path in ('/api/metadata', '/api/cover/remove') or path.startswith(('/api/releases/', '/api/library/')):
                return self._json(202, {'ok': True, 'queued': True})
            return self._json(200, {'ok': True})

    return Handler


def run(argv=None):
    """Run the LAN HTTP server on port 8081 by default (no drive access)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8081)
    parser.add_argument('--socket', default='/run/sonoscdplayer/control.sock')
    parser.add_argument('--state-dir', default=os.environ.get('STATE_DIRECTORY', '/var/lib/sonoscdplayer'))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('invalid port')
    server = ThreadingHTTPServer((args.host, args.port), make_handler(
        PlayerClient(args.socket), WebPreferences(args.state_dir)))
    server.daemon_threads = True
    print(f'[web] ready on port {server.server_address[1]} (LAN access only; no port forwarding)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
