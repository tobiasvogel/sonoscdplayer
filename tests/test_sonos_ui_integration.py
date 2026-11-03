"""M0.16b: regression tests for read-only UI and private event transport."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from sonos_ui.discovery import from_devices as speaker_snapshot, discover_speakers
from sonos_ui.experimental_frontend import action_for
from sonos_ui.input_adapter import to_ui_action
from sonos_ui.model import DisplayModel
from sonos_ui.multi_render import MultiRenderer
from sonos_ui.multi_target import MultiSelection, from_devices as group_snapshot
from sonos_ui.player_adapter import project_status
from sonos_ui.render import Renderer
from sonos_ui.ui_event_bridge import EventReceiver, publish_event, encode_event, decode_event


class UIIntegrationTests(unittest.TestCase):
    @staticmethod
    def grouped_devices():
        a = NS(uid='one', player_name='Büro')
        b = NS(uid='two', player_name='Küche')
        c = NS(uid='three', player_name='Wohnzimmer')
        group = NS(coordinator=a, members=(a,b))
        a.group = b.group = group
        c.group = NS(coordinator=c, members=(c,))
        return (a,b,c)

    def test_individual_names_even_in_existing_groups(self):
        topology = group_snapshot(self.grouped_devices())
        self.assertEqual([target.label for target in topology.targets], ['Büro','Küche','Wohnzimmer'])
        self.assertEqual(topology.targets[0].members, topology.targets[1].members)
        self.assertFalse(any('+N' in t.label for t in topology.targets))

    def test_marking_is_local_until_apply(self):
        state = MultiSelection().update(group_snapshot(self.grouped_devices()))
        marked = state.action('confirm')
        self.assertEqual(len(marked.draft), 1)
        self.assertFalse(marked.applied)

    def test_apply_request_still_has_no_sonos_effect(self):
        state = MultiSelection().update(group_snapshot(self.grouped_devices()))
        state = state.action('confirm').action('next').action('next').action('next').action('confirm')
        self.assertEqual(state.notice, 'status.apply_requested')
        self.assertFalse(state.applied)

    def test_missing_target_retains_draft_and_fails_closed(self):
        state = MultiSelection().update(group_snapshot(self.grouped_devices())).action('confirm')
        refreshed = state.update(group_snapshot(()))
        self.assertEqual(refreshed.draft, state.draft)
        self.assertEqual(refreshed.notice, 'status.missing_targets')

    def test_speaker_discovery_keeps_unicode_names(self):
        snapshot = speaker_snapshot(self.grouped_devices())
        self.assertEqual(snapshot.names, ('Büro','Küche','Wohnzimmer'))

    def test_discovery_failure_is_readonly_error_state(self):
        def unavailable(*,timeout):
            raise OSError('not reachable')
        self.assertEqual(discover_speakers(discover_fn=unavailable).error, 'OSError')

    def test_player_status_never_invents_metadata(self):
        state = {'schema_version':1,'player':{'mode':'STOPPED','track':1,'track_count':11}}
        display = project_status(state)
        self.assertTrue(display.disc_present)
        self.assertIsNone(display.cover)
        self.assertIsNone(display.album)
        self.assertIsNone(display.speaker)

    def test_invalid_player_schema_rejected(self):
        with self.assertRaises(ValueError):
            project_status({'schema_version':2,'player':{'mode':'STOPPED','track':1,'track_count':1}})

    def test_transport_buttons_not_redirected(self):
        for key in ('stop','playpause','skip','previous','eject','power'):
            self.assertIsNone(to_ui_action(key,'pressed'))

    def test_event_only_allowlist(self):
        self.assertIsNone(encode_event('stop','pressed'))
        self.assertIsNone(decode_event(b'{"name":"encoder","event":"right","uid":"secret"}'))
        self.assertEqual(decode_event(encode_event('encoder','right')), ('encoder','right'))
        self.assertEqual(action_for('encoder_sw','pressed'), 'confirm')

    def test_event_socket_is_private_and_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ui.sock'
            with EventReceiver(path) as receiver:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertTrue(publish_event(path,'encoder','right'))
                self.assertEqual(receiver.receive(),('encoder','right'))
            self.assertFalse(path.exists())

    def test_rgb_and_gray_are_exactly_128_square(self):
        render = Renderer()
        model = DisplayModel(view='menu',speakers=('Büro','Küche','Wohnzimmer'),focus_index=0)
        for gray in (False,True):
            self.assertEqual(render.render(model,grayscale=gray).size,(128,128))
            self.assertEqual(MultiRenderer().render_multi(
                MultiSelection().update(group_snapshot(self.grouped_devices())),gray=gray
            ).size,(128,128))


if __name__=='__main__':
    unittest.main()
