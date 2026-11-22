"""Metadata edits use a strictly validated, same-origin web-to-player RPC."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sonoscdplayer.web.server import make_handler


class FakePlayer:
    def __init__(self):
        self.commands = []

    def request(self, data, *, timeout=35):
        self.commands.append(data)
        return {'ok': True, 'queued': True}


class FakePreferences:
    pass


@contextmanager
def server():
    player = FakePlayer()
    instance = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(player, FakePreferences()))
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
            code, _ = post(base + '/api/metadata', bad)
            assert code == 400
        code, _ = post(base + '/api/metadata', {'token':'a'*32, 'field':'album',
                                'value':'Edited'}, Origin='http://untrusted.example')
        assert code == 403
        assert player.commands == []
