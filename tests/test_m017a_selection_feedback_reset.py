"""Offline regression tests for non-sticky selection feedback in read-only OLED mode."""
import unittest

from sonos_ui.i18n import Translator
from sonos_ui.multi_target import MultiSelection, Target, Topology
from sonos_ui.interactive_menu import InteractiveMenu


TARGETS = Topology((Target('RINCON_TEST1','Gym',('RINCON_TEST1',),('Gym',)),))


class SelectionFeedbackTests(unittest.TestCase):
    def test_old_error_disappears_when_navigating(self):
        state = MultiSelection(topology=TARGETS, focus=1,
                               notice='status.target_rejected')
        state = state.action('previous')
        self.assertEqual(state.focus, 0)
        self.assertEqual(state.notice, '')

    def test_old_error_disappears_when_checkbox_toggled(self):
        state = MultiSelection(topology=TARGETS,
                               notice='status.target_rejected')
        state = state.action('confirm')
        self.assertEqual(state.draft, frozenset({'RINCON_TEST1'}))
        self.assertEqual(state.notice, '')

    def test_disabled_rpc_reports_link_unavailable_not_invalid_selection(self):
        menu = InteractiveMenu(discover=lambda **kwargs: TARGETS)
        menu.active = True
        menu.state = MultiSelection(topology=TARGETS,
                                    draft=frozenset({'RINCON_TEST1'}),
                                    focus=1)
        menu.event('encoder_sw', 'pressed', now=1)
        self.assertEqual(menu.state.notice, 'status.target_unavailable')
        self.assertFalse(menu.state.applied)
        self.assertFalse(menu._target_pending)
        menu.event('encoder', 'left', now=2)
        self.assertEqual(menu.state.notice, '')
        self.assertFalse(menu.state.applied)

    def test_unavailable_message_is_localized(self):
        self.assertEqual(Translator('de').tr('status.target_unavailable'),
                         'Player-Anbindung inaktiv')
        self.assertEqual(Translator('en').tr('status.target_unavailable'),
                         'Player link disabled')


if __name__ == '__main__':
    unittest.main()
