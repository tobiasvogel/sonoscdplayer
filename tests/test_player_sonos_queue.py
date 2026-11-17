"""Playing from the Sonos queue so the app shows artist and album (17.11.)."""
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from player_controller import Controller
from player_state import Mode, PlayerState
from sonos_metadata import use_queue_from_config

from test_player_controller import FakePlayer, FakeServer

META = {'album': 'Album', 'artist': 'Artist',
        'tracks': [{'number': 1, 'title': 'One'}, {'number': 2, 'title': 'Two'}]}
FOREIGN = 'x-sonos-spotify:foreign'


def entry(uri):
    return SimpleNamespace(resources=[SimpleNamespace(uri=uri)])


class QueuePlayer(FakePlayer):
    def __init__(self, queue=(FOREIGN,), mode='NORMAL'):
        super().__init__()
        self.queue = [entry(u) for u in queue]
        self.play_mode = mode
        self.media_uri = ''
        self.direct = []
        outer = self

        class AV:
            def GetMediaInfo(self, _args):
                return {'CurrentURI': outer.media_uri}
        self.avTransport = AV()

    def add_to_queue(self, didl):
        url = re.search(r'<res[^>]*>([^<]+)</res>', didl).group(1)
        self.queue.append(entry(url))
        return len(self.queue)

    def play_from_queue(self, index):
        self.uri = self.queue[index].resources[0].uri
        self.media_uri = 'x-rincon-queue:TEST_UID#0'
        self.transport = 'TRANSITIONING'
        self.plays.append(self.uri)

    def play_uri(self, url, title='', meta=''):
        self.direct.append(url)
        super().play_uri(url, title)

    def get_queue(self, start=0, max_items=100):
        return self.queue[start:start + max_items]

    def remove_from_queue(self, index):
        del self.queue[index]

    def uris(self):
        return [e.resources[0].uri for e in self.queue]


class QueueTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('player_controller.didl_item', lambda didl: didl)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, player, metadata=META, use_queue=True):
        self.p, self.s = player, FakeServer()

        def verify(_uid):
            if self.p.transport != 'STOPPED':
                raise RuntimeError('not STOPPED')
            return self.p
        c = Controller(PlayerState().disc_loaded((1, 2)), 'TEST_UID', '192.0.2.10', 8765,
                       self.s, verify)
        c.metadata_snapshot, c.use_queue = metadata, use_queue
        self.c = c
        return c

    def playing(self):
        self.c.play()
        self.p.transport = 'PLAYING'
        self.c.refresh()
        self.assertEqual(self.c.state.mode, Mode.PLAYING)

    def test_appended_last_and_removed_on_stop(self):
        self.make(QueuePlayer())
        self.playing()
        url = 'http://192.0.2.10:8765/track/1.wav'
        self.assertEqual(self.p.uris(), [FOREIGN, url])
        self.assertEqual(self.p.direct, [])
        self.c.stop()
        self.assertEqual(self.p.uris(), [FOREIGN])

    def test_track_change_replaces_our_item(self):
        self.make(QueuePlayer())
        self.playing()
        self.c.change('next')
        self.assertEqual(self.p.uris(), [FOREIGN, 'http://192.0.2.10:8765/track/2.wav'])

    def test_changed_queue_is_left_alone(self):
        self.make(QueuePlayer())
        self.playing()
        self.p.queue.insert(0, entry('x-file:user-added'))   # our item moved
        self.c.stop()
        self.assertIn('http://192.0.2.10:8765/track/1.wav', self.p.uris())

    def test_shuffle_repeat_or_off_uses_direct_uri(self):
        for player, use_queue in ((QueuePlayer(mode='SHUFFLE'), True),
                                  (QueuePlayer(mode='REPEAT_ALL'), True),
                                  (QueuePlayer(), False)):
            with self.subTest(mode=player.play_mode, use_queue=use_queue):
                self.make(player, use_queue=use_queue)
                self.c.play()
                self.assertEqual(player.uris(), [FOREIGN])
                self.assertEqual(len(player.direct), 1)

    def test_without_metadata_direct_uri(self):
        self.make(QueuePlayer(), metadata=None)
        self.c.play()
        self.assertEqual(self.p.uris(), [FOREIGN])

    def test_natural_end_when_queue_jumps_back_to_first_entry(self):
        self.make(QueuePlayer())
        self.playing()
        self.p.transport, self.p.uri = 'STOPPED', FOREIGN      # Sonos wrapped around
        self.c.refresh()
        self.assertEqual(self.c.state.mode, Mode.STOPPED)
        self.assertEqual(self.p.uris(), [FOREIGN])
        self.assertEqual(self.p.stops, 0)

    def test_foreign_takeover_is_still_an_error(self):
        self.make(QueuePlayer())
        self.playing()
        self.p.queue.pop()                                  # our item removed by user
        self.p.transport, self.p.uri = 'STOPPED', FOREIGN
        self.c.refresh()
        self.assertEqual(self.c.state.mode, Mode.ERROR)

    def test_config(self):
        self.assertTrue(use_queue_from_config({}))
        self.assertFalse(use_queue_from_config({'sonos': {'use_queue': False}}))
        with self.assertRaises(ValueError):
            use_queue_from_config({'sonos': {'use_queue': 'yes'}})


if __name__ == '__main__':
    unittest.main()
