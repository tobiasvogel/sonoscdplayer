"""Track information and album art for Sonos."""
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from sonoscdplayer.player.controller import Controller
from sonoscdplayer.player.state import PlayerState
from sonoscdplayer.sonos.track_metadata import (DEFAULT_ALBUM_PREFIX, album_prefix_from_config, cover_route,
                            track_didl)

from test_player_controller import FakePlayer, FakeServer

COVER = '/var/lib/sonoscdplayer/covers/' + 'a' * 64 + '.jpg'
META = {'album': 'Album <1>', 'artist': 'Band & Co',
        'tracks': [{'number': 1, 'title': 'First', 'artist': None},
                   {'number': 2, 'title': None, 'artist': 'Guest'}],
        'cover_file': COVER}
NS = {'dc': 'http://purl.org/dc/elements/1.1/', 'upnp': 'urn:schemas-upnp-org:metadata-1-0/upnp/',
      'd': 'urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/'}


def fields(didl):
    item = ET.fromstring(didl).find('d:item', NS)
    get = lambda tag: (item.find(tag, NS).text if item.find(tag, NS) is not None else None)
    return {'title': get('dc:title'), 'creator': get('dc:creator'), 'album': get('upnp:album'),
            'art': get('upnp:albumArtURI'), 'res': get('d:res')}


class DidlTests(unittest.TestCase):
    def test_prefix_escaping_and_cover(self):
        didl = track_didl('http://192.0.2.10:8765/track/1.wav', META, 1,
                          cover_url='http://192.0.2.10:8765/cover/x.jpg')
        self.assertEqual(fields(didl), {
            'title': 'First', 'creator': 'Band & Co', 'album': 'CD: Album <1>',
            'art': 'http://192.0.2.10:8765/cover/x.jpg',
            'res': 'http://192.0.2.10:8765/track/1.wav'})

    def test_fallbacks_and_empty_prefix(self):
        got = fields(track_didl('http://h/track/2.wav', META, 2, album_prefix=''))
        self.assertEqual((got['title'], got['creator'], got['album'], got['art']),
                         ('Track 2', 'Guest', 'Album <1>', None))
        self.assertIsNone(track_didl('http://h/track/1.wav', None, 1))
        got = fields(track_didl('http://h/track/1.wav', {'tracks': []}, 1))
        self.assertIsNone(got['album'])     # no album name: no lone prefix

    def test_config(self):
        self.assertEqual(album_prefix_from_config({}), DEFAULT_ALBUM_PREFIX)
        self.assertEqual(album_prefix_from_config({'sonos': {'album_prefix': ''}}), '')
        self.assertEqual(album_prefix_from_config({'sonos': {'album_prefix': '💿 '}}), '💿 ')
        for bad in (5, 'x' * 25, 'a\nb'):
            with self.assertRaises(ValueError):
                album_prefix_from_config({'sonos': {'album_prefix': bad}})

    def test_cover_route_only_for_cache_names(self):
        self.assertEqual(cover_route(COVER), '/cover/' + 'a' * 64 + '.jpg')
        for bad in (None, '', '/etc/passwd', '/x/../' + 'a' * 63 + '.jpg'):
            self.assertIsNone(cover_route(bad))


class MetaPlayer(FakePlayer):
    def play_uri(self, url, title='', meta=''):
        self.meta = meta
        self.title = title
        super().play_uri(url, title)


class CoverServer(FakeServer):
    def start(self, track, cover=None):
        self.cover = cover
        super().start(track)


class ControllerTests(unittest.TestCase):
    def make(self, metadata, prefix=DEFAULT_ALBUM_PREFIX):
        self.player, self.server = MetaPlayer(), CoverServer()
        verify = lambda _uid: self.player
        c = Controller(PlayerState().disc_loaded((1, 2)), 'TEST_UID', '192.0.2.10', 8765,
                       self.server, verify)
        c.metadata_snapshot, c.album_prefix = metadata, prefix
        return c

    def test_play_sends_didl_and_offers_cover(self):
        self.make(META).play()
        got = fields(self.player.meta)
        self.assertEqual((got['title'], got['album']), ('First', 'CD: Album <1>'))
        self.assertEqual(got['art'], 'http://192.0.2.10:8765/cover/' + 'a' * 64 + '.jpg')
        self.assertEqual(self.server.cover, COVER)

    def test_without_metadata_unchanged(self):
        self.make(None).play()
        self.assertEqual((self.player.meta, self.player.title), ('', 'SonosCDPlayer Track 1'))
        self.assertIsNone(getattr(self.server, 'cover', None))

    def test_status_stays_compact(self):
        c = self.make(META)
        snap = c.status_snapshot()['metadata']
        self.assertEqual(snap['track_title'], 'First')
        self.assertNotIn('tracks', snap)


class CoverEndpointTests(unittest.TestCase):
    def test_server_serves_only_the_cover_route(self):
        from sonoscdplayer.audio import track_http_server
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'b' * 64 + '.jpg')
            with open(path, 'wb') as handle:
                handle.write(b'\xff\xd8jpeg')
            route, data = track_http_server.load_cover(path)
            self.assertEqual((route, data), ('/cover/' + 'b' * 64 + '.jpg', b'\xff\xd8jpeg'))
            self.assertEqual(track_http_server.load_cover(os.path.join(tmp, 'x.jpg')), (None, None))
            self.assertEqual(track_http_server.load_cover(None), (None, None))


if __name__ == '__main__':
    unittest.main()
