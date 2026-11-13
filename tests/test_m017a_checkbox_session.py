"""Opt-in group checkbox UI session tests (no GPIO, network, or Sonos writes)."""
import unittest
from sonos_ui.session import UISession
from sonos_ui.checkbox_selection import SpeakerChoice, TargetChoice


CHOICES = (
    SpeakerChoice('A', 'Hobbyraum', True, True),
    SpeakerChoice('B', 'Musikzimmer', True, True),
    SpeakerChoice('C', 'Gästezimmer', True, True),
)


class CheckboxSessionTests(unittest.TestCase):
    def setUp(self):
        self.ui = UISession()
        self.ui.set_speaker_choices(CHOICES)
        self.assertIsNone(self.ui.input('open_speakers', 1))

    def test_checkbox_toggle_does_not_confirm_or_play(self):
        self.assertIsNone(self.ui.input('confirm', 2))
        self.assertEqual(self.ui.snapshot().checked_indices, (0,))
        self.assertIsNone(self.ui.checkbox_selection.confirmed)

    def test_two_checked_create_group_plan_not_applied_target(self):
        self.ui.input('confirm', 2)
        self.ui.input('navigate_next', 3)
        self.ui.input('confirm', 4)
        plan = self.ui.input('apply_selection', 5)
        self.assertEqual(plan, TargetChoice('A', ('B',)))
        self.assertIsNone(self.ui.checkbox_selection.confirmed)
        self.assertTrue(self.ui.navigation.menu_open)
        self.ui.acknowledge_selection(plan, now_ms=6)
        self.assertEqual(self.ui.checkbox_selection.confirmed, plan)
        self.assertFalse(self.ui.navigation.menu_open)

    def test_failed_ack_keeps_last_applied_target(self):
        self.ui.input('confirm', 2)
        plan = self.ui.input('apply_selection', 3)
        self.ui.acknowledge_selection(plan, now_ms=4)
        self.ui.input('open_speakers', 5)
        self.ui.input('navigate_next', 6)
        self.ui.input('confirm', 7)
        self.assertEqual(self.ui.input('apply_selection', 8).member_uids, ('B',))
        self.assertEqual(self.ui.checkbox_selection.confirmed, TargetChoice('A', ()))
        self.ui.input('back', 9)
        self.assertEqual(self.ui.snapshot().checked_indices, (0,))

    def test_refresh_order_does_not_change_uid_selection(self):
        self.ui.input('confirm', 2)
        self.ui.set_speaker_choices(tuple(reversed(CHOICES)))
        self.assertEqual(self.ui.snapshot().checked_indices, (2,))
        self.assertEqual(self.ui.input('apply_selection', 3), TargetChoice('A', ()))

    def test_legacy_solo_selection_intent_remains_available(self):
        ui = UISession()
        ui.set_speaker_names(['Hobbyraum', 'Musikzimmer'])
        ui.input('open_speakers', 1)
        intent = ui.input('confirm', 2)
        self.assertEqual((intent.speaker_index, intent.speaker_name), (0, 'Hobbyraum'))


if __name__ == '__main__':
    unittest.main()
