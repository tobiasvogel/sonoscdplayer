"""Speaker selection in the menu: checkboxes, single/multi target, apply feedback and the player RPC boundary (no Sonos)."""
import time
import unittest
from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS
from sonos_ui.checkbox_selection import CheckboxSelection, SpeakerChoice, TargetChoice
from sonos_ui.session import UISession
from sonos_ui.checkbox_target_worker import apply_selection
from sonos_ui.multi_target import MultiSelection, from_devices, Topology, Target
from sonos_ui.single_target import select_one, TargetSelectionError
from sonos_ui.player_command_boundary import (
    ExplicitTransport,
    TransportRejected,
    request_for,
    TargetIntent,
)
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.i18n import Translator
from sonos_ui.selection_plan import plan_from_applied
CHOICES = (
    SpeakerChoice('A', 'Hobbyraum', True, True),
    SpeakerChoice('B', 'Musikzimmer', True, True),
    SpeakerChoice('C', 'Gästezimmer', True, True),
)
TOPOLOGY = Topology((
    Target('A', 'Hobbyraum', ('A',), ('Hobbyraum',)),
    Target('B', 'Musikzimmer', ('B',), ('Musikzimmer',)),
))
TARGETS = Topology((Target('RINCON_TEST1','Hobbyraum',('RINCON_TEST1',),('Hobbyraum',)),))



def speakers():
    return [
        SpeakerChoice('A', 'Hobbyraum', True, True),
        SpeakerChoice('B', 'Musikzimmer', True, True),
        SpeakerChoice('C', 'Gästezimmer', True, True),
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
        for choice in (SpeakerChoice('A', 'Hobbyraum', True, False),
                       SpeakerChoice('A', 'Hobbyraum', False, True)):
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


class TargetBridgeTests(unittest.TestCase):
    def setUp(self):
        self.ui = UISession()
        self.ui.set_speaker_choices((
            SpeakerChoice('A', 'Hobbyraum', True, True),
            SpeakerChoice('B', 'Musikzimmer', True, True),
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
            SpeakerChoice('A', 'Hobbyraum', True, True),
            SpeakerChoice('B', 'Musikzimmer', False, True),
        ))
        calls = []
        with self.assertRaisesRegex(RuntimeError, 'grouped or active'):
            apply_selection(self.ui, self.plan, socket_path='unused',
                            submit=lambda *a, **k: calls.append(1))
        self.assertEqual(calls, [])


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


def topology(grouped=False):
    a=NS(uid='RINCON_123',player_name='Gästezimmer')
    b=NS(uid='RINCON_234',player_name='Küche')
    if grouped:
        g=NS(coordinator=a,members=(a,b))
        a.group=b.group=g
        return from_devices((a,b))
    a.group=NS(coordinator=a,members=(a,))
    b.group=NS(coordinator=b,members=(b,))
    return from_devices((a,b))


def confirmed():
    state=MultiSelection().update(topology()).action('confirm')
    # Model the independent positive player ACK; menu click alone never confirms.
    return replace(state,applied=state.draft)


class PlayerCommandBoundaryTests(unittest.TestCase):
    def test_confirm_only_stores_target_and_never_sends(self):
        sent=[]
        bridge=ExplicitTransport(sender=lambda req: sent.append(req),discover=lambda **kw:topology())
        self.assertEqual(bridge.confirm(confirmed()),'Gästezimmer')
        self.assertEqual(sent,[])
        self.assertEqual(bridge.target.uid,'RINCON_123')

    def test_dispatch_disabled_by_default(self):
        bridge=ExplicitTransport(sender=lambda req:self.fail('must not send'),discover=lambda **kw:topology())
        bridge.confirm(confirmed())
        with self.assertRaises(TransportRejected):
            bridge.dispatch('playpause')

    def test_explicit_action_sends_uid_only_after_fresh_check(self):
        sent=[]
        bridge=ExplicitTransport(sender=lambda request:sent.append(request) or True,
                                 discover=lambda **kw:topology(),enabled=True)
        bridge.confirm(confirmed())
        self.assertTrue(bridge.dispatch('playpause'))
        self.assertEqual(sent,[{'command':'playpause','uid':'RINCON_123'}])

    def test_group_transition_fails_closed_and_clears_target(self):
        sent=[]
        bridge=ExplicitTransport(sender=lambda req:sent.append(req),discover=lambda **kw:topology(True),enabled=True)
        bridge.confirm(confirmed())
        with self.assertRaises(TransportRejected):
            bridge.dispatch('playpause')
        self.assertIsNone(bridge.target)
        self.assertEqual(sent,[])

    def test_discovery_failure_fails_closed(self):
        bridge=ExplicitTransport(sender=lambda req:self.fail('must not send'),
                                 discover=lambda **kw:Topology((),'Offline'),enabled=True)
        bridge.confirm(confirmed())
        with self.assertRaises(TransportRejected):
            bridge.dispatch('playpause')

    def test_unbound_transport_actions_are_forbidden(self):
        bridge=ExplicitTransport(sender=lambda req:self.fail('must not send'),
                                 discover=lambda **kw:topology(),enabled=True)
        bridge.confirm(confirmed())
        for action in ('stop','next','previous','play'):
            with self.subTest(action=action),self.assertRaises(ValueError):
                bridge.dispatch(action)

    def test_invalid_uid_rejected_before_rpc(self):
        with self.assertRaises(TransportRejected):
            request_for('playpause',TargetIntent('invalid uid','Speaker'))

    def test_no_target_fails_closed(self):
        bridge=ExplicitTransport(enabled=True,discover=lambda **kw:topology())
        with self.assertRaises(TransportRejected):
            bridge.dispatch('playpause')


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
        from sonos_ui.interactive_menu import ACCEPTED_NOTICE_SECONDS
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
