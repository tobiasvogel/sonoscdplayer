"""Sonos playback start and speaker switching with simulated speakers."""
import pathlib
import queue
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
TARGET_UID = 'TEST_TARGET_UID'
UID = 'TEST_TARGET_UID'
OLD = 'http://192.0.2.12:8765/track/2.wav'
NEW = 'http://192.0.2.12:8765/track/3.wav'
from sonoscdplayer.sonos.playback_start import check_target, wait_reader
from sonoscdplayer.sonos.play_track import make_url, prepare_selection, confirmed_mode
from sonoscdplayer.player.state import Mode, PlayerState, TransitionError
from sonoscdplayer.sonos.switch_track import ownership_snapshot, stopped_snapshot, switch_owned



class DummyPlayer:
    def __init__(self, uid=TARGET_UID, state='STOPPED', other=False):
        self.uid = uid
        self.player_name = 'Test Speaker'
        self.other = other
        self._state = state
        members = [self]
        if other:
            members.append(SimpleNamespace(uid='TEST_OTHER_UID', player_name='Other Test Speaker'))
        self.group = SimpleNamespace(members=members, coordinator=self)
    def get_current_transport_info(self):
        return {'current_transport_state': self._state}


class TestSafety(unittest.TestCase):
    def test_solo_stopped(self):
        self.assertEqual(check_target(DummyPlayer(), TARGET_UID), 'STOPPED')
    def test_group_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Einzelgruppe'):
            check_target(DummyPlayer(other=True), TARGET_UID)
    def test_playing_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'STOPPED'):
            check_target(DummyPlayer(state='PLAYING'), TARGET_UID)
    def test_uid_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'UID'):
            check_target(DummyPlayer(uid='wrong'), TARGET_UID)
    def test_wait_reader(self):
        q = queue.Queue()
        proc = SimpleNamespace(poll=lambda: None)
        q.put((0, 'Serving http://0.0.0.0:8765/track/1.wav'))
        q.put((0, '[+0.01s] READER started'))
        wait_reader(q, proc, .1)
    def test_reader_finished_refused(self):
        q = queue.Queue()
        proc = SimpleNamespace(poll=lambda: None)
        q.put((0, 'Serving http://0.0.0.0:8765/track/1.wav'))
        q.put((0, '[+0.01s] READER end written=0 error=X'))
        with self.assertRaisesRegex(RuntimeError, 'bereits fertig'):
            wait_reader(q, proc, .1)


class PlayTests(unittest.TestCase):
    def setUp(self):
        self.disc = SimpleNamespace(tracks=[SimpleNamespace(number=i) for i in range(1, 4)],
                                    get_track=lambda n: {1: 'one', 2: 'two', 3: 'three'}[n])

    def test_selected_track_two(self):
        state = prepare_selection(self.disc, 2)
        self.assertEqual((state.mode, state.selected_track), (Mode.STOPPED, 2))

    def test_out_of_range_rejected(self):
        with self.assertRaises(TransitionError): prepare_selection(self.disc, 4)

    def test_state_confirmed_only_by_playing(self):
        state = prepare_selection(self.disc, 2).start_requested()
        self.assertEqual(confirmed_mode(state, 'TRANSITIONING').mode, Mode.STARTING)
        self.assertEqual(confirmed_mode(state, 'STOPPED').mode, Mode.STARTING)
        self.assertEqual(confirmed_mode(state, 'PLAYING').mode, Mode.PLAYING)

    def test_natural_end_after_confirmed_playback(self):
        state = prepare_selection(self.disc, 2).start_requested().playback_confirmed()
        self.assertEqual(confirmed_mode(state, 'STOPPED').mode, Mode.STOPPED)

    def test_url_selects_track_without_private_defaults(self):
        self.assertEqual(make_url('192.0.2.12', 8765, 2), 'http://192.0.2.12:8765/track/2.wav')

    def test_url_rejects_invalid_values(self):
        for host in ['localhost', '127.0.0.1', '0.0.0.0', '192.0.2.3/x', 'bad']:
            with self.subTest(host=host), self.assertRaises(ValueError): make_url(host, 8765, 2)
        with self.assertRaises(ValueError): make_url('192.0.2.12', 0, 2)
        with self.assertRaises(ValueError): make_url('192.0.2.12', 8765, 0)


def fake(transport='PLAYING', uri=OLD, solo=True, coordinator=True):
    p = Mock()
    p.uid = UID
    p.group = SimpleNamespace(members=[p] if solo else [p, Mock(uid='OTHER')],
                              coordinator=p if coordinator else Mock(uid='OTHER'))
    p.get_current_transport_info.return_value = {'current_transport_state': transport}
    p.get_current_track_info.return_value = {'uri': uri}
    return p


class OwnershipTests(unittest.TestCase):
    def test_owned(self): ownership_snapshot(fake(), UID, OLD)
    def test_rejects_wrong_uri(self):
        for uri in ('', NEW, 'x-rincon:other', None):
            with self.subTest(uri=uri), self.assertRaises(RuntimeError): ownership_snapshot(fake(uri=uri), UID, OLD)
    def test_rejects_not_playing(self):
        for status in ('STOPPED', 'TRANSITIONING', 'PAUSED_PLAYBACK'):
            with self.subTest(status=status), self.assertRaises(RuntimeError): ownership_snapshot(fake(transport=status), UID, OLD)
    def test_rejects_group_or_coordinator(self):
        for p in (fake(solo=False), fake(coordinator=False)):
            with self.assertRaises(RuntimeError): ownership_snapshot(p, UID, OLD)
    def test_rejects_wrong_uid(self):
        with self.assertRaises(RuntimeError): ownership_snapshot(fake(), 'WRONG', OLD)
    def test_stopped_snapshot(self):
        stopped_snapshot(fake(transport='STOPPED'), UID)
        with self.assertRaises(RuntimeError): stopped_snapshot(fake(), UID)
    def test_switch_order_and_guard(self):
        p = fake()
        transitions = []
        def stop():
            transitions.append('STOP')
            p.get_current_transport_info.return_value = {'current_transport_state':'STOPPED'}
        def play(url, title):
            transitions.append(('PLAY',url))
        switch_owned(p, UID, OLD, NEW, stop=stop, play=play)
        self.assertEqual(transitions, ['STOP',('PLAY',NEW)])
        self.assertEqual(p.get_current_track_info.call_count, 2)
    def test_never_stop_wrong_source(self):
        p = fake(uri=NEW)
        with self.assertRaises(RuntimeError): switch_owned(p, UID, OLD, NEW)
        p.stop.assert_not_called()
        p.play_uri.assert_not_called()
    def test_never_play_when_after_stop_group_changes(self):
        p = fake()
        def stop():
            p.get_current_transport_info.return_value = {'current_transport_state':'STOPPED'}
            p.group.members.append(Mock(uid='OTHER'))
        with self.assertRaises(RuntimeError): switch_owned(p, UID, OLD, NEW, stop=stop)
        p.play_uri.assert_not_called()


if __name__ == '__main__':
    unittest.main()
