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
from urllib.parse import urlsplit, parse_qs

from .player_client import PlayerClient, PlayerUnavailable, command_payload, metadata_edit_payload, release_payload
from .state import WebPreferences
from .dashboard_projection import public_dashboard, public_release_detail


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
                     'track_artist', 'match_status', 'lookup_status')},
        'has_cover': bool(metadata.get('cover_file')),
        'progress': {k: progress.get(k) for k in ('elapsed_seconds', 'duration_seconds',
                     'position', 'duration', 'elapsed', 'total')},
        'sonos': {k: sonos.get(k) for k in ('transport', 'owned', 'group_cleanup_pending')},
        'target_rev': state.get('target_rev'),
        'last_rejection': {'seq': rejection.get('seq'), 'reason': reason[:160]},
    }


def make_handler(player: PlayerClient, preferences: WebPreferences):
    """Build an HTTP handler that can be exercised against a fake player."""
    class Handler(BaseHTTPRequestHandler):
        server_version = 'SonosCDPlayerWeb'
        sys_version = ''

        def log_message(self, _format, *_args):
            # No URLs, speaker names, user input or secrets in the journal.
            pass

        def _json(self, code, payload):
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
            if path == '/api/theme':
                return self._json(200, {'theme': preferences.read()})
            if path == '/api/status':
                try:
                    return self._json(200, public_status(player.request({'command': 'status'}, timeout=3)))
                except PlayerUnavailable:
                    return self._json(503, {'ok': False, 'error': 'Player nicht erreichbar'})
            if path == '/api/dashboard':
                try:
                    return self._json(200, public_dashboard(
                        player.request({'command': 'dashboard'}, timeout=3)))
                except (PlayerUnavailable, ValueError):
                    return self._json(503, {'ok': False, 'error': 'Dashboard nicht erreichbar'})
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
                        'error': 'Lautsprecherwahl derzeit nicht verfügbar' if not result.get('ok') else None,
                    })
                except PlayerUnavailable:
                    return self._json(503, {'ok': False, 'error': 'Player nicht erreichbar'})
            if path == '/api/release-detail':
                try:
                    query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
                    if set(query) != {'token', 'release_id'} or any(len(v) != 1 for v in query.values()):
                        raise ValueError('invalid release detail query')
                    result = player.request(release_payload({key: value[0] for key, value in query.items()},
                                                            'detail'), timeout=5)
                    if not result.get('ok'):
                        return self._json(409, {'ok': False, 'error': 'Release nicht mehr verfügbar'})
                    return self._json(200, public_release_detail(result.get('release_detail')))
                except ValueError:
                    return self._json(400, {'ok': False, 'error': 'Ungültige Release-Abfrage'})
                except PlayerUnavailable:
                    return self._json(503, {'ok': False, 'error': 'Player nicht erreichbar'})
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
                        return self._json(404, {'error': 'cover not available'})
                    kind = 'image/png' if candidate.suffix.lower() == '.png' else 'image/jpeg'
                    return self._file(candidate.read_bytes(), kind)
                except (PlayerUnavailable, OSError, ValueError):
                    return self._json(404, {'error': 'cover not available'})
            self._json(404, {'error': 'not found'})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in ('/api/command', '/api/theme', '/api/metadata',
                            '/api/releases/search', '/api/releases/select'):
                return self._json(404, {'error': 'not found'})
            origin = self.headers.get('Origin')
            if origin:
                url = urlsplit(origin)
                if url.scheme not in ('http', 'https') or url.netloc != self.headers.get('Host'):
                    return self._json(403, {'error': 'cross-origin request forbidden'})
            if self.headers.get_content_type() != 'application/json':
                return self._json(415, {'error': 'JSON required'})
            try:
                size = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= size <= MAX_BODY:
                    raise ValueError('invalid content length')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('JSON object required')
                if path == '/api/theme':
                    if set(data) != {'theme'}:
                        raise ValueError('theme is required')
                    preferences.set_theme(data['theme'])
                    return self._json(200, {'ok': True, 'theme': preferences.read()})
                if path == '/api/metadata':
                    request = metadata_edit_payload(data)
                elif path.startswith('/api/releases/'):
                    request = release_payload(data, path.rsplit('/', 1)[1])
                else:
                    request = command_payload(data)
            except (ValueError, TypeError):
                return self._json(400, {'ok': False, 'error': 'Ungültige Eingabe'})
            except OSError:
                return self._json(503, {'ok': False, 'error': 'Einstellung nicht speicherbar'})
            try:
                result = player.request(request, timeout=35)
            except PlayerUnavailable:
                return self._json(503, {'ok': False, 'error': 'Ergebnis unbekannt; Status prüfen, nicht erneut senden'})
            # Hide raw player diagnostics containing installation-specific details.
            if not result.get('ok'):
                return self._json(409, {'ok': False, 'error': 'Befehl vom Player abgelehnt; Status prüfen'})
            if path == '/api/metadata' or path.startswith('/api/releases/'):
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
