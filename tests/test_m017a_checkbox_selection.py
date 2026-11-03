"""Offline checkbox selection tests; no Sonos, CD or GPIO dependencies."""
import unittest

from sonos_ui.checkbox_selection import CheckboxSelection, SpeakerChoice, TargetChoice


def speakers():
    return [
        SpeakerChoice('A', 'Gym', True, True),
        SpeakerChoice('B', 'Praxis', True, True),
        SpeakerChoice('C', 'Büro', True, True),
    ]


class CheckboxSelectionTests(unittest.TestCase):
    def setUp(self):
        self.selection = CheckboxSelection()

    def test_empty_draft_cannot_be_submitted(self):
        with self.assertRaisesRegex(ValueError, 'at least one'):
            self.selection.preview(speakers())

    def test_single_speaker_preserves_solo_behavior(self):
        self.selection.toggle('A')
        self.assertEqual(self.selection.preview(speakers()), TargetChoice('A', ()))
        self.assertFalse(self.selection.preview(speakers()).grouped)

    def test_two_speakers_choose_alphabetically_first_coordinator(self):
        self.selection.toggle('B')
        self.selection.toggle('A')
        self.assertEqual(self.selection.preview(speakers()),
                         TargetChoice('A', ('B',)))
        self.assertTrue(self.selection.preview(speakers()).grouped)

    def test_uncheck_alphabetical_coordinator_promotes_remaining(self):
        self.selection.toggle('A')
        self.selection.toggle('B')
        self.selection.toggle('A')
        self.assertEqual(self.selection.preview(speakers()), TargetChoice('B', ()))

    def test_discovery_reorder_does_not_change_coordinator(self):
        self.selection.toggle('A')
        self.selection.toggle('B')
        self.assertEqual(self.selection.preview(list(reversed(speakers()))),
                         TargetChoice('A', ('B',)))

    def test_similar_names_do_not_collapse_distinct_uids(self):
        self.selection.toggle('A')
        self.selection.toggle('B')
        same_names = [SpeakerChoice('A', 'Zimmer', True, True),
                      SpeakerChoice('B', 'Zimmer', True, True)]
        self.assertEqual(self.selection.preview(same_names),
                         TargetChoice('A', ('B',)))

    def test_reverse_checkbox_order_and_discovery_order_use_same_coordinator(self):
        self.selection.toggle('B')
        self.selection.toggle('A')
        self.assertEqual(self.selection.preview(list(reversed(speakers()))),
                         TargetChoice('A', ('B',)))

    def test_equal_names_break_tie_by_uid(self):
        self.selection.toggle('B')
        self.selection.toggle('A')
        choices = [SpeakerChoice('B', 'Zimmer', True, True),
                   SpeakerChoice('A', 'Zimmer', True, True)]
        self.assertEqual(self.selection.preview(choices), TargetChoice('A', ('B',)))

    def test_alphabetical_names_not_uid_or_checkbox_order(self):
        self.selection.toggle('A')
        self.selection.toggle('B')
        choices = [SpeakerChoice('A', 'Zentrum', True, True),
                   SpeakerChoice('B', 'Alpha', True, True)]
        self.assertEqual(self.selection.preview(choices), TargetChoice('B', ('A',)))

    def test_missing_speaker_blocks_plan_without_fallback(self):
        self.selection.toggle('A')
        self.selection.toggle('B')
        with self.assertRaisesRegex(RuntimeError, 'no longer available'):
            self.selection.preview(speakers()[:1])
        self.assertEqual(self.selection.checked_uids, ('A', 'B'))

    def test_active_or_grouped_speaker_blocks_plan(self):
        self.selection.toggle('A')
        for choice in (SpeakerChoice('A', 'Gym', True, False),
                       SpeakerChoice('A', 'Gym', False, True)):
            with self.assertRaisesRegex(RuntimeError, 'grouped or active'):
                self.selection.preview([choice])

    def test_duplicate_uid_blocks_ambiguous_discovery(self):
        self.selection.toggle('A')
        with self.assertRaisesRegex(ValueError, 'duplicate speaker UID'):
            self.selection.preview(speakers() + [speakers()[0]])

    def test_failed_acknowledgement_does_not_replace_confirmed_target(self):
        self.selection.toggle('A')
        original = self.selection.preview(speakers())
        self.selection.acknowledge(original)
        self.selection.toggle('B')
        draft = self.selection.preview(speakers())
        # RPC failure / no positive ACK -> do not call acknowledge.
        self.assertNotEqual(draft, original)
        self.assertEqual(self.selection.confirmed, original)
        self.selection.cancel()
        self.assertEqual(self.selection.checked_uids, ('A',))

    def test_stale_acknowledgement_is_rejected(self):
        self.selection.toggle('A')
        plan = self.selection.preview(speakers())
        self.selection.toggle('B')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.selection.acknowledge(plan)
        self.assertIsNone(self.selection.confirmed)

    def test_reopening_can_restore_confirmed_group(self):
        self.selection.toggle('B')
        self.selection.toggle('A')
        plan = self.selection.preview(speakers())
        self.selection.acknowledge(plan)
        self.selection.begin()
        self.assertEqual(self.selection.checked_uids, ('A', 'B'))
        self.assertEqual(self.selection.confirmed, plan)

    def test_no_implicit_confirmation(self):
        self.selection.toggle('A')
        self.selection.preview(speakers())
        self.assertIsNone(self.selection.confirmed)


if __name__ == '__main__':
    unittest.main()
