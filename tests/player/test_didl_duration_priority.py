"""Prefer the physical CD track duration in Sonos DIDL resources."""
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from sonoscdplayer.player.controller import Controller
from sonoscdplayer.player.state import PlayerState
from sonoscdplayer.sonos.track_metadata import track_didl
from test_player_controller import FakeServer
from test_player_sonos_queue import QueuePlayer


URL = 'http://192.0.2.10:8765/session/' + 'a' * 32 + '/track/1.wav'
NS = {'d': 'urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/'}


def resource(didl):
    return ET.fromstring(didl).find('d:item/d:res', NS)


def metadata(seconds):
    return {'tracks': [{'number': 1, 'title': 'First', 'duration_seconds': seconds}]}


@pytest.mark.parametrize('external', [63.0, 10.0])
def test_physical_duration_overrides_conflicting_metadata(external):
    res = resource(track_didl(URL, metadata(external), 1, duration_seconds=21.72))
    assert res.get('duration') == '0:00:22'
    assert res.text == URL
    assert res.get('protocolInfo') == 'http-get:*:audio/wav:*'


@pytest.mark.parametrize('physical', [None, 0, -1, True, '22', float('nan'), float('inf')])
def test_valid_metadata_is_fallback_for_unusable_physical_duration(physical):
    res = resource(track_didl(URL, metadata(63.0), 1, duration_seconds=physical))
    assert res.get('duration') == '0:01:03'


@pytest.mark.parametrize('external', [None, 0, -1, True, '63'])
def test_valid_physical_duration_with_unusable_metadata(external):
    res = resource(track_didl(URL, metadata(external), 1, duration_seconds=21.72))
    assert res.get('duration') == '0:00:22'


def test_omit_duration_when_neither_source_is_usable():
    res = resource(track_didl(URL, metadata(None), 1, duration_seconds=0))
    assert 'duration' not in res.attrib


def test_no_metadata_retains_plain_play_uri_contract():
    assert track_didl(URL, None, 1, duration_seconds=21.72) is None


@pytest.mark.parametrize('use_queue', [False, True], ids=['direct', 'queue'])
def test_controller_sends_physical_duration_for_each_resource(monkeypatch, use_queue):
    monkeypatch.setattr('sonoscdplayer.player.controller.didl_item', lambda didl: didl)
    player = QueuePlayer()
    server = FakeServer()
    controller = Controller(PlayerState().disc_loaded((1, 2)), 'TEST_UID',
                            '192.0.2.10', 8765, server, lambda uid: player)
    controller.use_queue = use_queue
    controller.metadata_snapshot = {
        'tracks': [{'number': 1, 'title': 'First', 'duration_seconds': 63.0},
                   {'number': 2, 'title': 'Second', 'duration_seconds': 199.0}],
    }
    physical = {1: 21.72, 2: 85.5067}
    controller.display_toc = SimpleNamespace(
        get_track=lambda track: SimpleNamespace(duration_seconds=physical[track]))
    direct_didls = []
    original_play_uri = player.play_uri

    def capture(url, title='', meta=''):
        direct_didls.append(meta)
        original_play_uri(url, title, meta)

    monkeypatch.setattr(player, 'play_uri', capture)
    controller.play()
    session = server.options['session_id']
    expected = [('0:00:22', f'http://192.0.2.10:8765/session/{session}/track/1.wav')]
    if use_queue:
        expected.append(('0:01:26', f'http://192.0.2.10:8765/session/{session}/track/2.wav'))
    resources = [resource(didl) for didl in (player.didls if use_queue else direct_didls)]
    assert [(res.get('duration'), res.text) for res in resources] == expected
