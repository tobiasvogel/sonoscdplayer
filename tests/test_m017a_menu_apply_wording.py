"""Offline acceptance tests: one-click apply, no review screen or playback."""
import unittest
from sonos_ui.i18n import Translator
from sonos_ui.multi_target import MultiSelection, Target, Topology
from sonos_ui.selection_plan import plan_from_applied


class ApplySelectionMenuTests(unittest.TestCase):
    def setUp(self):
        self.topology = Topology((
            Target('A', 'Hobbyraum', ('A',), ('Hobbyraum',)),
            Target('B', 'Musikzimmer', ('B',), ('Musikzimmer',)),
        ))

    def test_action_wording_is_apply_not_review(self):
        self.assertEqual(Translator('de').tr('menu.review'), 'Auswahl übernehmen')
        self.assertEqual(Translator('en').tr('menu.review'), 'Apply selection')

    def test_single_selected_directly_requests_apply_without_confirming(self):
        state = MultiSelection(topology=self.topology).action('confirm')
        state = state.action('next').action('next').action('confirm')
        self.assertEqual(state.notice, 'status.apply_requested')
        self.assertEqual(state.applied, frozenset())
        self.assertEqual(plan_from_applied(state.topology.targets, state.draft).members, ())

    def test_group_directly_requests_apply_without_confirming(self):
        state = MultiSelection(topology=self.topology).action('confirm')
        state = state.action('next').action('confirm')
        state = state.action('next').action('confirm')
        self.assertEqual(state.notice, 'status.apply_requested')
        self.assertEqual(state.applied, frozenset())
        plan = plan_from_applied(state.topology.targets, state.draft)
        self.assertEqual((plan.coordinator, plan.members), ('A', ('B',)))

    def test_empty_selection_rejected(self):
        state = MultiSelection(topology=self.topology)
        state = state.action('next').action('next').action('confirm')
        self.assertEqual(state.notice, 'status.invalid_selection')

    def test_discovery_missing_speaker_preserves_confirmed_target(self):
        state = MultiSelection(topology=self.topology,
                               draft=frozenset({'A','B'}),
                               applied=frozenset({'A'}))
        refreshed = state.update(Topology((self.topology.targets[1],)))
        self.assertEqual(refreshed.applied, frozenset({'A'}))
        self.assertEqual(refreshed.draft, frozenset({'A','B'}))
        self.assertEqual(refreshed.notice, 'status.missing_targets')


if __name__ == '__main__':
    unittest.main()
