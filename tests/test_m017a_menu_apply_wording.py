"""Offline acceptance tests for the existing apply-selection menu semantics."""
import unittest

from sonos_ui.i18n import Translator
from sonos_ui.multi_target import MultiSelection, Target, Topology
from sonos_ui.selection_plan import plan_from_applied


class ApplySelectionMenuTests(unittest.TestCase):
    def setUp(self):
        self.topology = Topology((
            Target('A', 'Gym', ('A',), ('Gym',)),
            Target('B', 'Praxis', ('B',), ('Praxis',)),
        ))

    def test_action_wording_is_apply_not_review(self):
        self.assertEqual(Translator('de').tr('menu.review'), 'Auswahl übernehmen')
        self.assertEqual(Translator('en').tr('menu.review'), 'Apply selection')
        self.assertEqual(Translator('de').tr('status.selection_no_play'),
                         'PLAY startet separat')

    def test_single_selected_review_and_confirmation_produce_solo_plan(self):
        state = MultiSelection(topology=self.topology)
        state = state.action('confirm')
        state = state.action('next').action('next')
        state = state.action('confirm')
        self.assertTrue(state.reviewing)
        self.assertEqual(state.applied, frozenset())
        state = state.action('confirm')
        self.assertEqual(state.notice, 'status.local_applied')
        self.assertEqual(plan_from_applied(state.topology.targets, state.applied).members, ())

    def test_two_selected_review_and_confirmation_produce_group_plan(self):
        state = MultiSelection(topology=self.topology)
        state = state.action('confirm')
        state = state.action('next').action('confirm')
        state = state.action('next').action('confirm')
        self.assertTrue(state.reviewing)
        self.assertEqual(state.applied, frozenset())
        state = state.action('confirm')
        plan = plan_from_applied(state.topology.targets, state.applied)
        self.assertEqual((plan.coordinator, plan.members), ('A', ('B',)))

    def test_no_selection_cannot_open_apply_review(self):
        state = MultiSelection(topology=self.topology)
        state = state.action('next').action('next').action('confirm')
        self.assertFalse(state.reviewing)
        self.assertEqual(state.notice, 'status.invalid_selection')


if __name__ == '__main__':
    unittest.main()
