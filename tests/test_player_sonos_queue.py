"""The whole CD as one block in the Sonos queue (D-230): Sonos plays on by
itself, the Sonos app may skip, player and OLED follow (17.11.)."""
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from player_controller import Controller
from player_state import Mode, PlayerState
from sonos_metadata import use_queue_from_config

from test_player_controller import FakePlayer, FakeServer

META = {'album': 'Album', 'artist': 'Artist',
        'tracks': [{'number': 1, 'title': 'One'}, {'number': 2, 'title': 'Two'},
                   {'number': 3, 'title': 'Three'}]}
FOREIGN = 'x-sonos-spotify:foreign'
URL = 'http://192.0.2.10:8765/track/{}.wav'


def entry(uri):
    return SimpleNamespace(resources=[SimpleNamespace(uri=uri)])


class QueuePlayer(FakePlayer):
    def __init__(self, queue=(FOREIGN,), mode='NORMAL'):
        super().__init__()
        self.queue = [entry(u) for u in queue]
        self.play_mode = mode
        self.media_uri = ''
        self.direct = []
        self.didls = []
        outer = self

        class AV:
            def GetMediaInfo(self, _args):
                return {'CurrentURI': outer.media_uri}
        self.avTransport = AV()

    def add_to_queue(self, didl):
        self.didls.append(didl)
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


def block():
    return [URL.format(n) for n in (1, 2, 3)]


class QueueTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('player_controller.didl_item', lambda didl: didl)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, player, metadata=META, use_queue=True, tracks=(1, 2, 3)):
        self.p, self.s = player, FakeServer()

        def verify(_uid):
            if self.p.transport != 'STOPPED':
                raise RuntimeError('not STOPPED')
            return self.p
        c = Controller(PlayerState().disc_loaded(tracks), 'TEST_UID', '192.0.2.10', 8765,
                       self.s, verify)
        c.metadata_snapshot, c.use_queue = metadata, use_queue
        self.c = c
        return c

    def playing(self):
        self.c.play()
        self.p.transport = 'PLAYING'
        self.c.refresh()
        self.assertEqual(self.c.state.mode, Mode.PLAYING)

    def test_whole_disc_appended_and_selected_track_started(self):
        self.make(QueuePlayer())
        self.c.state = self.c.state.select(2)
        self.playing()
        self.assertEqual(self.p.uris(), [FOREIGN] + block())
        self.assertEqual(self.p.uri, URL.format(2))
        self.assertEqual(self.s.options.get('disc'), True)
        self.assertIn('<dc:title>Two</dc:title>', self.p.didls[1])
        self.assertEqual(self.p.direct, [])

    def test_without_metadata_titles_are_track_numbers(self):
        self.make(QueuePlayer(), metadata=None)
        self.playing()
        self.assertIn('<dc:title>Track 3</dc:title>', self.p.didls[2])

    def test_stop_removes_only_our_block(self):
        self.make(QueuePlayer())
        self.playing()
        self.c.stop()
        self.assertEqual(self.p.uris(), [FOREIGN])

    def test_changed_entries_are_left_alone(self):
        self.make(QueuePlayer())
        self.playing()
        self.p.queue[2] = entry('x-file:user-replaced')     # track 2 slot changed
        self.c.stop()
        self.assertEqual(self.p.uris(), [FOREIGN, 'x-file:user-replaced'])

    def test_player_follows_skips_in_the_sonos_app(self):
        self.make(QueuePlayer())
        self.playing()
        self.p.uri = URL.format(3)                          # app: jump to track 3
        self.c.refresh()
        self.assertEqual((self.c.state.mode, self.c.state.selected_track), (Mode.PLAYING, 3))
        self.assertEqual(self.c.owned_url, URL.format(3))
        self.assertEqual(self.c.status_snapshot()['player']['track'], 3)

    def test_device_next_moves_within_the_block(self):
        self.make(QueuePlayer())
        self.playing()
        self.c.change('next')
        self.assertEqual(self.p.uri, URL.format(2))
        self.assertEqual(self.c.state.selected_track, 2)
        self.assertEqual(self.p.stops, 0)
        self.assertEqual(len(self.p.didls), 3)              # nothing re-added

    def test_end_of_last_track_with_wrap_is_a_natural_end(self):
        self.make(QueuePlayer())
        self.playing()
        self.p.uri = URL.format(3)
        self.c.refresh()
        self.p.transport, self.p.uri = 'STOPPED', FOREIGN    # queue wrapped to its start
        self.c.refresh()
        self.assertEqual(self.c.state.mode, Mode.STOPPED)
        self.assertEqual(self.p.uris(), [FOREIGN])

    def test_wrap_onto_our_own_first_track(self):
        self.make(QueuePlayer(queue=()))
        self.playing()
        self.p.uri = URL.format(3)
        self.c.refresh()
        self.p.transport, self.p.uri = 'STOPPED', URL.format(1)
        self.c.refresh()
        self.assertEqual((self.c.state.mode, self.c.state.selected_track), (Mode.STOPPED, 1))
        self.assertEqual(self.p.uris(), [])

    def test_foreign_takeover_is_still_an_error(self):
        self.make(QueuePlayer())
        self.playing()
        self.p.queue.pop()                                  # our last entry removed by user
        self.p.transport, self.p.uri = 'STOPPED', FOREIGN
        self.c.refresh()
        self.assertEqual(self.c.state.mode, Mode.ERROR)

    def test_foreign_uri_while_playing_is_an_error(self):
        self.make(QueuePlayer())
        self.playing()
        self.p.uri = FOREIGN                                # user played own music
        self.c.refresh()
        self.assertEqual(self.c.state.mode, Mode.ERROR)

    def test_startup_failure_removes_our_block(self):
        self.make(QueuePlayer())
        self.c.play()
        self.p.transport = 'STOPPED'                    # Sonos never started
        for later in (30, 36):
            with patch('player_controller.time.monotonic',
                       return_value=self.c.start_monotonic + later):
                self.c.refresh()
        self.assertEqual(self.c.state.mode, Mode.ERROR)
        self.assertEqual(self.p.uris(), [FOREIGN])
        self.assertIsNone(self.c._queue_block)

    def test_shuffle_repeat_or_off_uses_direct_uri(self):
        for player, use_queue in ((QueuePlayer(mode='SHUFFLE'), True),
                                  (QueuePlayer(mode='REPEAT_ALL'), True),
                                  (QueuePlayer(), False)):
            with self.subTest(mode=player.play_mode, use_queue=use_queue):
                self.make(player, use_queue=use_queue)
                self.c.play()
                self.assertEqual(player.uris(), [FOREIGN])
                self.assertEqual(len(player.direct), 1)
                self.assertNotIn('disc', self.s.options)

    def test_failed_enqueue_takes_back_partial_block(self):
        player = QueuePlayer()
        original = player.add_to_queue

        def flaky(didl):
            if len(player.didls) == 2:
                raise RuntimeError('UPnP 701')
            return original(didl)
        player.add_to_queue = flaky
        self.make(player)
        with self.assertRaises(RuntimeError):
            self.c.play()
        self.assertEqual(player.uris(), [FOREIGN])
        self.assertEqual(self.c.state.mode, Mode.ERROR)
        self.assertIsNone(self.c._queue_block)

    def test_config(self):
        self.assertTrue(use_queue_from_config({}))
        self.assertFalse(use_queue_from_config({'sonos': {'use_queue': False}}))
        with self.assertRaises(ValueError):
            use_queue_from_config({'sonos': {'use_queue': 'yes'}})


if __name__ == '__main__':
    unittest.main()
