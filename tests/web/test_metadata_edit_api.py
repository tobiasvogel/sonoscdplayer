"""Metadata edits use a strictly validated, same-origin web-to-player RPC."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sonoscdplayer.web.server import make_handler
from sonoscdplayer.web.state import WebPreferences
from sonoscdplayer.ui.i18n.translator import catalog


class FakePlayer:
    def __init__(self):
        self.commands = []

    def request(self, data, *, timeout=35):
        self.commands.append(data)
        return {'ok': True, 'queued': True}


@contextmanager
def server(language='de'):
    # Exercise the real preferences contract; the HTTP error translator needs
    # its state directory even when an invalid request never reaches the player.
    with tempfile.TemporaryDirectory(prefix='sonoscdplayer-web-meta-test-') as state_dir:
        (Path(state_dir) / 'ui.json').write_text(
            json.dumps({'version': 1, 'language': language}), encoding='utf-8')
        player = FakePlayer()
        instance = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
            player, WebPreferences(state_dir)))
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            yield 'http://127.0.0.1:' + str(instance.server_port), player
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(2)


def post(url, data, **headers):
    request = Request(url, data=json.dumps(data).encode(), method='POST',
                      headers={'Content-Type': 'application/json', **headers})
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_metadata_edit_forwards_only_one_validated_field():
    with server() as (base, player):
        code, payload = post(base + '/api/metadata', {
            'token': 'a' * 32, 'field': 'track_title', 'track': 2, 'value': 'New title'})
        assert code == 202 and payload['ok'] and payload['queued']
        assert player.commands == [{'command': 'edit-metadata', 'token': 'a' * 32,
                                    'field': 'track_title', 'track': 2, 'value': 'New title'}]


def test_rejects_stale_or_malformed_requests_before_player_socket():
    with server() as (base, player):
        for bad in (
            {'token': 'a' * 31, 'field': 'album', 'value': 'Title'},
            {'token': 'a' * 32, 'field': 'track_title', 'value': 'Title'},
            {'token': 'a' * 32, 'field': 'album', 'value': 'X' * 161},
            {'token': 'a' * 32, 'field': 'album', 'value': 'Album', 'uid': 'speaker'},
        ):
            code, payload = post(base + '/api/metadata', bad)
            assert code == 400
            assert payload['error'] == catalog('de')['web.api.invalid_input']
        code, payload = post(base + '/api/metadata', {'token':'a'*32, 'field':'album',
                                'value':'Edited'}, Origin='http://untrusted.example')
        assert code == 403
        assert payload['error'] == catalog('de')['web.api.cross_origin']
        assert player.commands == []


def test_invalid_metadata_request_is_localized_in_english_without_player_action():
    with server('en') as (base, player):
        code, payload = post(base + '/api/metadata', {
            'token': 'invalid', 'field': 'album', 'value': 'Title'})
        assert code == 400
        assert payload['error'] == catalog('en')['web.api.invalid_input']
        assert player.commands == []
