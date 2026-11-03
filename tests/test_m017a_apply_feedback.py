"""Offline interactive-menu selection feedback: no review screen, no playback."""
import unittest

from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.multi_target import Target, Topology


TOPOLOGY = Topology((
    Target('A', 'Gym', ('A',), ('Gym',)),
    Target('B', 'Praxis', ('B',), ('Praxis',)),
))


class ApplyFeedbackTests(unittest.TestCase):
    def new_menu(self, *, solo=None, group=None):
        menu = InteractiveMenu(
            discover=lambda **kwargs: TOPOLOGY,
            target_rpc=solo,
            group_target_rpc=group,
            refresh_seconds=1000,
        )
        menu.active = True
        menu.state = menu.state.update(TOPOLOGY)
        return menu

    def apply(self, menu, count=1):
        self.assertTrue(menu.event('encoder_sw', 'pressed', 1))
        if count == 2:
            self.assertTrue(menu.event('encoder', 'right', 2))
            self.assertTrue(menu.event('encoder_sw', 'pressed', 3))
        for i in range(2 if count == 1 else 1):
            self.assertTrue(menu.event('encoder', 'right', 4+i))
        self.assertTrue(menu.event('encoder_sw', 'pressed', 7))
        self.assertEqual(menu.state.notice, 'status.target_pending')
        menu._target_worker.join(timeout=2)
        self.assertFalse(menu._target_worker.is_alive())
        menu.poll(8)

    def test_single_confirm_sends_once_then_acks(self):
        calls = []
        menu = self.new_menu(solo=lambda uid: (calls.append(uid) or True))
        self.apply(menu)
        self.assertEqual(calls, ['A'])
        self.assertEqual(menu.state.applied, frozenset({'A'}))
        self.assertEqual(menu.state.notice, 'status.target_accepted')

    def test_group_confirm_sends_once_then_acks(self):
        calls = []
        menu = self.new_menu(solo=lambda uid: self.fail('wrong solo RPC'),
                             group=lambda uid, members: (calls.append((uid, members)) or True))
        self.apply(menu, count=2)
        self.assertEqual(calls, [('A', ('B',))])
        self.assertEqual(menu.state.applied, frozenset({'A','B'}))
        self.assertEqual(menu.state.notice, 'status.target_accepted')

    def test_rejected_rpc_keeps_last_acknowledged_target(self):
        menu = self.new_menu(solo=lambda uid: False)
        menu.state = menu.state.__class__(
            topology=TOPOLOGY,
            draft=frozenset({'A'}),
            applied=frozenset({'B'}),
            focus=2,
        )
        self.assertTrue(menu.event('encoder_sw', 'pressed', 1))
        menu._target_worker.join(timeout=2)
        menu.poll(2)
        self.assertEqual(menu.state.notice, 'status.target_rejected')
        self.assertEqual(menu.state.applied, frozenset({'B'}))

    def test_missing_group_rpc_rejects_without_changing_target(self):
        menu = self.new_menu(solo=lambda uid: True)
        menu.state = menu.state.__class__(
            topology=TOPOLOGY,
            draft=frozenset({'A','B'}),
            applied=frozenset({'A'}),
            focus=2,
        )
        self.assertTrue(menu.event('encoder_sw', 'pressed', 1))
        self.assertFalse(menu._target_pending)
        self.assertEqual(menu.state.notice, 'status.target_unavailable')
        self.assertEqual(menu.state.applied, frozenset({'A'}))


if __name__ == '__main__':
    unittest.main()
