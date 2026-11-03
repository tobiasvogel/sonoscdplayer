"""M0.17a safety tests: selecting a UI target has no Sonos side effects."""
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS

from sonos_ui.multi_target import MultiSelection, from_devices, Topology
from sonos_ui.single_target import select_one, TargetSelectionError


def target_topology(*, grouped=False):
    a=NS(uid='speaker-1',player_name='Büro')
    b=NS(uid='speaker-2',player_name='Küche')
    if grouped:
        group=NS(coordinator=a,members=(a,b))
        a.group=b.group=group
        return from_devices((a,b))
    a.group=NS(coordinator=a,members=(a,))
    b.group=NS(coordinator=b,members=(b,))
    return from_devices((a,b))


class SingleTargetTests(unittest.TestCase):
    def test_one_confirmed_solo_speaker_is_intent_only(self):
        state=MultiSelection().update(target_topology())
        state=state.action('confirm')
        state=replace(state,applied=state.draft)
        selected=select_one(state)
        self.assertEqual((selected.uid,selected.display_name),('speaker-1','Büro'))

    def test_unconfirmed_selection_rejected(self):
        state=MultiSelection().update(target_topology()).action('confirm')
        with self.assertRaises(TargetSelectionError):
            select_one(state)

    def test_no_selection_rejected(self):
        with self.assertRaises(TargetSelectionError):
            select_one(MultiSelection().update(target_topology()))

    def test_two_speakers_rejected(self):
        state=MultiSelection().update(target_topology())
        state=state.action('confirm').action('next').action('confirm')
        state=replace(state,applied=state.draft)
        with self.assertRaises(TargetSelectionError):
            select_one(state)

    def test_existing_group_rejected(self):
        state=MultiSelection().update(target_topology(grouped=True))
        state=state.action('confirm')
        state=replace(state,applied=state.draft)
        with self.assertRaises(TargetSelectionError):
            select_one(state)

    def test_stale_or_failed_discovery_rejected(self):
        state=MultiSelection().update(target_topology())
        state=state.action('confirm')
        state=replace(state,applied=state.draft)
        with self.assertRaises(TargetSelectionError):
            select_one(replace(state,topology=Topology((), 'DiscoveryError')))

    def test_apply_request_is_not_confirmation(self):
        state=MultiSelection().update(target_topology()).action('confirm')
        # The menu's one-click apply request is not a successful player ACK.
        state=replace(state,focus=2).action('confirm')
        with self.assertRaises(TargetSelectionError):
            select_one(state)


if __name__=='__main__':
    unittest.main()
