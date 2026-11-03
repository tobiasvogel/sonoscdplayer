"""No-network tests for the explicit checkbox -> player target bridge."""
import unittest

from sonos_ui.checkbox_selection import SpeakerChoice, TargetChoice
from sonos_ui.checkbox_target_worker import apply_selection
from sonos_ui.session import UISession


class TargetBridgeTests(unittest.TestCase):
    def setUp(self):
        self.ui = UISession()
        self.ui.set_speaker_choices((
            SpeakerChoice('A', 'Gym', True, True),
            SpeakerChoice('B', 'Praxis', True, True),
        ))
        self.ui.input('open_speakers', 1)
        self.ui.input('confirm', 2)
        self.ui.input('navigate_next', 3)
        self.ui.input('confirm', 4)
        self.plan = self.ui.input('apply_selection', 5)

    def test_group_members_passed_to_set_target_without_playback(self):
        calls = []
        def fake_submit(uid, *, members, socket_path):
            calls.append((uid, members, socket_path))
            return True
        self.assertTrue(apply_selection(self.ui, self.plan,
                                        socket_path='/tmp/test-control.sock',
                                        submit=fake_submit))
        self.assertEqual(calls, [('A', ('B',), '/tmp/test-control.sock')])
        self.assertIsNone(self.ui.checkbox_selection.confirmed)
        self.ui.acknowledge_selection(self.plan, now_ms=6)
        self.assertEqual(self.ui.checkbox_selection.confirmed, self.plan)

    def test_rejected_rpc_preserves_previous_acknowledgment(self):
        self.assertIsNone(self.ui.checkbox_selection.confirmed)
        with self.assertRaisesRegex(RuntimeError, 'player did not acknowledge'):
            apply_selection(self.ui, self.plan, socket_path='unused',
                            submit=lambda uid, *, members, socket_path: False)
        self.assertIsNone(self.ui.checkbox_selection.confirmed)

    def test_exception_from_rpc_is_not_swallowed(self):
        def fail(uid, *, members, socket_path):
            raise ConnectionError('player socket unavailable')
        with self.assertRaises(ConnectionError):
            apply_selection(self.ui, self.plan, socket_path='unused', submit=fail)
        self.assertIsNone(self.ui.checkbox_selection.confirmed)

    def test_new_checkbox_state_invalidates_old_plan(self):
        self.ui.input('navigate_previous', 6)
        self.ui.input('confirm', 7)
        called = []
        with self.assertRaisesRegex(RuntimeError, 'selection changed'):
            apply_selection(self.ui, self.plan, socket_path='unused',
                            submit=lambda *a, **k: called.append(1))
        self.assertEqual(called, [])

    def test_active_speaker_blocks_rpc_before_submit(self):
        self.ui.set_speaker_choices((
            SpeakerChoice('A', 'Gym', True, True),
            SpeakerChoice('B', 'Praxis', False, True),
        ))
        calls = []
        with self.assertRaisesRegex(RuntimeError, 'grouped or active'):
            apply_selection(self.ui, self.plan, socket_path='unused',
                            submit=lambda *a, **k: calls.append(1))
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
