"""Speaker selection in the menu: checkboxes, single/multi target, apply feedback and the player RPC boundary (no Sonos)."""
import time
import unittest
from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS
from sonoscdplayer.ui.menu.speakers.multi_target import MultiSelection, from_devices, Topology, Target
from sonoscdplayer.ui.menu.speakers.single_target import select_one, TargetSelectionError
from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
from sonoscdplayer.ui.i18n import Translator
from sonoscdplayer.ui.menu.speakers.selection_plan import plan_from_applied
TOPOLOGY = Topology((
    Target('A', 'Hobbyraum', ('A',), ('Hobbyraum',)),
    Target('B', 'Musikzimmer', ('B',), ('Musikzimmer',)),
))
TARGETS = Topology((Target('RINCON_TEST1','Hobbyraum',('RINCON_TEST1',),('Hobbyraum',)),))



def target_topology(*, grouped=False):
    a=NS(uid='speaker-1',player_name='Gästezimmer')
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
        self.assertEqual((selected.uid,selected.display_name),('speaker-1','Gästezimmer'))

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
        self.assertEqual(menu.connect_notices.current(now=8).key, 'status.target_accepted')

    def test_group_confirm_sends_once_then_acks(self):
        calls = []
        menu = self.new_menu(solo=lambda uid: self.fail('wrong solo RPC'),
                             group=lambda uid, members: (calls.append((uid, members)) or True))
        self.apply(menu, count=2)
        self.assertEqual(calls, [('A', ('B',))])
        self.assertEqual(menu.state.applied, frozenset({'A','B'}))
        self.assertEqual(menu.connect_notices.current(now=8).key, 'status.target_accepted')

    def test_accepted_selection_shows_success_notice_then_closes(self):
        from sonoscdplayer.ui.menu.interactive_menu import ACCEPTED_NOTICE_SECONDS
        menu = self.new_menu(solo=lambda uid: True)
        self.apply(menu)                                  # poll(8) receives the ACK
        notice = menu.connect_notices.current(now=8)
        self.assertEqual((notice.key, notice.severity), ('status.target_accepted', 'success'))
        self.assertEqual(notice.duration_seconds, ACCEPTED_NOTICE_SECONDS)
        self.assertEqual(menu.state.notice, '', 'no tiny status line any more')
        self.assertTrue(menu.active, 'notice is visible')
        menu.poll(8 + ACCEPTED_NOTICE_SECONDS)
        self.assertFalse(menu.active, 'menu closed after the notice')
        self.assertEqual(menu.state.applied, frozenset({'A'}))

    def test_rejected_selection_keeps_menu_open(self):
        menu = self.new_menu(solo=lambda uid: False)
        self.apply(menu)
        menu.poll(100)
        self.assertTrue(menu.active)
        self.assertEqual(menu.state.notice, 'status.target_rejected')

    def test_power_menu_keeps_unapplied_checkboxes(self):
        menu = self.new_menu(solo=lambda uid: True)
        menu._power_action = lambda _choice: True
        menu.section = 'targets'
        self.assertTrue(menu.event('encoder_sw', 'pressed', 1))   # tick first speaker
        draft = menu.state.draft
        self.assertTrue(draft)
        self.assertTrue(menu.event('power', 'short', 2))
        self.assertTrue(menu.event('power', 'short', 3))          # back
        self.assertTrue(menu.active and not menu.power_menu)
        self.assertEqual(menu.section, 'targets')
        self.assertEqual(menu.state.draft, draft)

    def test_accept_while_power_menu_open_returns_to_overview(self):
        menu = self.new_menu(solo=lambda uid: True)
        menu._power_action = lambda _choice: True
        self.assertTrue(menu.event('encoder_sw', 'pressed', 1))
        for i in range(2):
            self.assertTrue(menu.event('encoder', 'right', 2 + i))
        self.assertTrue(menu.event('encoder_sw', 'pressed', 5))   # apply
        self.assertTrue(menu.event('power', 'short', 6))
        menu._target_worker.join(timeout=2)
        menu.poll(7)
        self.assertEqual(menu.state.applied, frozenset({'A'}))
        menu.poll(9)
        self.assertTrue(menu.power_menu, 'power menu is not closed underneath')
        self.assertTrue(menu.event('power', 'short', 20))
        self.assertFalse(menu.active)

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


def accepted_notice(menu, now):
    current = menu.connect_notices.current(now=now)
    return (current.key, current.severity) if current is not None else None


def handoff_topology(grouped=False):
    a=NS(uid='RINCON_X1',player_name='Office')
    b=NS(uid='RINCON_X2',player_name='Kitchen')
    if grouped:
        group=NS(coordinator=a,members=(a,b))
        a.group=b.group=group
    else:
        a.group=NS(coordinator=a,members=(a,))
        b.group=NS(coordinator=b,members=(b,))
    return from_devices((a,b))


def menu_confirm(menu):
    menu.event('speaker_select','pressed',now=0)
    menu.state=menu.state.update(handoff_topology())
    # Speakers are sorted by display name (Kitchen precedes Office).
    # Explicitly focus the Office target rather than assuming index zero.
    office_focus = next(i for i, target in enumerate(menu.state.topology.targets)
                        if target.key == 'RINCON_X1')
    menu.state=replace(menu.state,focus=office_focus)
    menu.event('encoder_sw','pressed')
    menu.state=replace(menu.state,focus=len(menu.state.topology.targets))
    menu.event('encoder_sw','pressed')


class MenuTargetRPCTests(unittest.TestCase):
    def test_opt_in_disabled_never_sends(self):
        menu=InteractiveMenu(discover=lambda **kw: handoff_topology())
        menu_confirm(menu)
        self.assertEqual(menu.state.notice,'status.target_unavailable')
        self.assertFalse(menu.state.applied)

    def test_exactly_one_confirmed_uid_sent_once(self):
        sent=[]
        menu=InteractiveMenu(discover=lambda **kw: handoff_topology(),
                             target_rpc=lambda uid: sent.append(uid) or True)
        menu_confirm(menu)
        menu._target_worker.join(timeout=2)
        self.assertTrue(menu.poll(now=0))
        self.assertEqual(sent,['RINCON_X1'])
        self.assertEqual(accepted_notice(menu,0),('status.target_accepted','success'))
        self.assertFalse(menu._target_pending)

    def test_rejection_is_visible_and_not_retried(self):
        called=[]
        def reject(uid):
            called.append(1)
            raise RuntimeError('private backend error')
        menu=InteractiveMenu(discover=lambda **kw:handoff_topology(),target_rpc=reject)
        menu_confirm(menu)
        menu._target_worker.join(timeout=2)
        menu.poll(now=0)
        self.assertEqual(called,[1])
        self.assertEqual(menu.state.notice,'status.target_rejected')

    def test_pending_blocks_encoder_retrigger(self):
        started=Event()
        release=Event()
        sent=[]
        def slow(uid):
            sent.append(uid)
            started.set()
            release.wait(2)
            return True
        menu=InteractiveMenu(discover=lambda **kw:handoff_topology(),target_rpc=slow)
        menu_confirm(menu)
        self.assertTrue(started.wait(2))
        try:
            self.assertFalse(menu.event('encoder_sw','pressed'))
            self.assertEqual(len(sent),1)
        finally:
            release.set()
            menu._target_worker.join(timeout=2)

    def test_multiple_speakers_rejected_without_rpc(self):
        sent=[]
        menu=InteractiveMenu(discover=lambda **kw:handoff_topology(),
                             target_rpc=lambda uid:sent.append(uid) or True)
        menu.event('speaker_select','pressed',now=0)
        menu.state=menu.state.update(handoff_topology())
        menu.event('encoder_sw','pressed')
        menu.event('encoder','right')
        menu.event('encoder_sw','pressed')
        menu.state=replace(menu.state,focus=2)
        menu.event('encoder_sw','pressed')
        self.assertEqual(sent,[])
        self.assertEqual(menu.state.notice,'status.target_unavailable')

    def test_no_acknowledgment_is_rejection(self):
        menu=InteractiveMenu(discover=lambda **kw:handoff_topology(),
                             target_rpc=lambda uid:False)
        menu_confirm(menu)
        menu._target_worker.join(timeout=2)
        menu.poll(now=0)
        self.assertEqual(menu.state.notice,'status.target_rejected')


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
