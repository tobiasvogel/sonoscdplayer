"""Integration tests for the web slice: no Sonos or optical hardware."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import socketserver
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from sonoscdplayer.web.player_client import PlayerClient, command_payload
from sonoscdplayer.web.server import make_handler, public_status
from sonoscdplayer.web.state import WebPreferences


class FakePlayer(socketserver.StreamRequestHandler):
    """A local player stub responding to the real newline JSON protocol."""

    def handle(self):
        request = json.loads(self.rfile.readline().decode('utf-8'))
        self.server.requests.append(request)
        if request['command'] == 'status':
            reply = {
                'ok': True,
                'state': {
                    'schema_version': 1,
                    'player': {'mode': 'PLAYING', 'track': 2, 'track_count': 5},
                    'metadata': {'album': 'Record', 'artist': 'Artist',
                                 'track_title': 'Song', 'track_artist': 'Artist',
                                 'cover_file': '/home/person/private.jpg'},
                    'sonos': {'target_uid': 'RINCON_FAKE123', 'owned': True,
                              'transport': 'PLAYING'},
                    'target_rev': 1,
                    'last_rejection': {'seq': 2, 'reason': 'speaker RINCON_FAKE123 at 192.0.2.11'}
                },
            }
        elif request['command'] == 'get-target':
            reply = {'ok': True, 'target_selection': {
                'selected': True, 'speakers': ['RINCON_FAKE123']}}
        else:
            reply = {'ok': True, 'state': {}}
        self.wfile.write((json.dumps(reply) + '\n').encode('utf-8'))


class PlayerServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


@contextmanager
def running_server(tmp_path):
    player_server = PlayerServer(str(tmp_path / 'player.sock'), FakePlayer)
    player_server.requests = []
    player_thread = threading.Thread(target=player_server.serve_forever, daemon=True)
    player_thread.start()
    web_server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
        PlayerClient(tmp_path / 'player.sock'), WebPreferences(tmp_path / 'state')))
    web_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
    web_thread.start()
    try:
        yield f'http://127.0.0.1:{web_server.server_address[1]}', player_server.requests
    finally:
        web_server.shutdown(); web_server.server_close()
        player_server.shutdown(); player_server.server_close()


def get_json(url):
    with urlopen(url, timeout=4) as result:
        return json.load(result)


def post_json(url, value, *, origin=None):
    headers = {'Content-Type': 'application/json'}
    if origin:
        headers['Origin'] = origin
    request = Request(url, data=json.dumps(value).encode(), headers=headers)
    with urlopen(request, timeout=4) as result:
        return json.load(result)


def test_status_projection_never_exposes_private_ids_or_paths():
    response = {'ok': True, 'state': {
        'player': {'mode': 'PLAYING', 'track': 1, 'track_count': 2},
        'sonos': {'target_uid': 'RINCON_REAL', 'owned': True, 'transport': 'PLAYING'},
        'metadata': {'album': 'Example', 'cover_file': '/home/name/cover.jpg'},
        'last_rejection': {'seq': 1, 'reason': 'RINCON_ABC at 192.0.2.10'},
    }}
    result = public_status(response)
    serialized = json.dumps(result)
    assert 'RINCON_' not in serialized
    assert '/home/' not in serialized
    assert '192.0.2.10' not in serialized
    assert result['has_cover'] is True
    assert result['metadata']['album'] == 'Example'


def test_theme_is_atomic_and_only_in_state_dir(tmp_path):
    pref = WebPreferences(tmp_path / 'var')
    assert pref.read() == 'auto'
    pref.set_theme('dark')
    assert pref.read() == 'dark'
    assert (tmp_path / 'var' / 'web.json').stat().st_mode & 0o777 == 0o600
    assert list((tmp_path / 'var').iterdir()) == [tmp_path / 'var' / 'web.json']
    with pytest.raises(ValueError):
        pref.set_theme('sepia')
    (tmp_path / 'var' / 'web.json').write_text('{broken', encoding='utf-8')
    assert pref.read() == 'auto'


def test_api_reads_status_and_updates_theme(tmp_path):
    with running_server(tmp_path) as (url, requests):
        assert get_json(url + '/api/theme') == {'theme': 'auto'}
        assert post_json(url + '/api/theme', {'theme': 'dark'}) == {'ok': True, 'theme': 'dark'}
        assert get_json(url + '/api/theme')['theme'] == 'dark'
        state = get_json(url + '/api/status')
        assert state['player']['track'] == 2
        assert state['metadata']['track_title'] == 'Song'
        assert state['has_cover'] is True
        assert requests == [{'command': 'status'}]
        targets = get_json(url + '/api/targets')
        assert targets['target_selection'] == {'selected': True, 'speakers_count': 1}
        assert 'RINCON' not in json.dumps(targets)
        with urlopen(url + '/') as response:
            page = response.read().decode('utf-8')
            assert 'theme-switcher' in page and 'Jetzt wiedergeben' in page
        with urlopen(url + '/app.js') as response:
            assert 'play-selected' in response.read().decode('utf-8')


def test_control_commands_are_forwarded_exactly_once(tmp_path):
    with running_server(tmp_path) as (url, requests):
        for command in ('play-selected', 'pause', 'resume', 'next', 'previous',
                        'stop', 'eject', 'volume +1', 'reset-disc'):
            assert post_json(url + '/api/command', {'command': command})['ok']
        assert requests == [{'command': command} for command in
                            ('play-selected', 'pause', 'resume', 'next', 'previous',
                             'stop', 'eject', 'volume +1', 'reset-disc')]


@pytest.mark.parametrize('value', [
    {'command': 'stop', 'force': True},
    {'command': 'resolve-group-recovery'},
    {'command': 'play', 'uid': 'RINCON_1'},
    {'command': 'volume +99'},
    {'command': 'stop', 'uid': 'RINCON_1'},
])
def test_reject_unsafe_commands(value):
    with pytest.raises(ValueError):
        command_payload(value)


def test_reject_other_origin_and_non_json(tmp_path):
    with running_server(tmp_path) as (url, requests):
        with pytest.raises(HTTPError) as error:
            post_json(url + '/api/command', {'command': 'stop'}, origin='https://elsewhere.example')
        assert error.value.code == 403
        with pytest.raises(HTTPError) as error:
            with urlopen(Request(url + '/api/command', data=b'command=stop',
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})):
                pass
        assert error.value.code == 415
        assert requests == []


def test_no_player_does_not_fabricate_success(tmp_path):
    with running_server(tmp_path) as (url, _):
        pass
    # Real application behavior is a 503 for an absent player socket.
    handler = make_handler(PlayerClient(tmp_path / 'absent.sock'), WebPreferences(tmp_path))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(f'http://127.0.0.1:{server.server_address[1]}/api/status', timeout=4)
        assert error.value.code == 503
    finally:
        server.shutdown(); server.server_close()
