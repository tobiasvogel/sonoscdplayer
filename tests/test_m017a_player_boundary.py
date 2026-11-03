"""Offline checks for disabled-by-default player RPC boundary."""
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from sonos_ui.multi_target import MultiSelection, from_devices, Topology
from sonos_ui.player_command_boundary import (
    ExplicitTransport, TransportRejected, request_for, TargetIntent,
)


def topology(grouped=False):
    a=NS(uid='RINCON_123',player_name='Büro')
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
        self.assertEqual(bridge.confirm(confirmed()),'Büro')
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


if __name__=='__main__':
    unittest.main()
