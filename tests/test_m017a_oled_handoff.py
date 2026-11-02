"""Offline end-to-end menu confirmation to injectable player RPC tests."""
import time
import unittest
from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS

from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.multi_target import from_devices


def topology(grouped=False):
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
    menu.event('encoder_sw','pressed',now=0)
    menu.state=menu.state.update(topology())
    # Speakers are sorted by display name (Kitchen precedes Office).
    # Explicitly focus the Office target rather than assuming index zero.
    office_focus = next(i for i, target in enumerate(menu.state.topology.targets)
                        if target.key == 'RINCON_X1')
    menu.state=replace(menu.state,focus=office_focus)
    menu.event('encoder_sw','pressed')
    menu.state=replace(menu.state,focus=len(menu.state.topology.targets))
    menu.event('encoder_sw','pressed')
    menu.event('encoder_sw','pressed')


class MenuTargetRPCTests(unittest.TestCase):
    def test_opt_in_disabled_never_sends(self):
        menu=InteractiveMenu(discover=lambda **kw: topology())
        menu_confirm(menu)
        self.assertEqual(menu.state.notice,'status.local_applied')

    def test_exactly_one_confirmed_uid_sent_once(self):
        sent=[]
        menu=InteractiveMenu(discover=lambda **kw: topology(),
                             target_rpc=lambda uid: sent.append(uid) or True)
        menu_confirm(menu)
        menu._target_worker.join(timeout=2)
        self.assertTrue(menu.poll(now=0))
        self.assertEqual(sent,['RINCON_X1'])
        self.assertEqual(menu.state.notice,'status.target_accepted')
        self.assertFalse(menu._target_pending)

    def test_rejection_is_visible_and_not_retried(self):
        called=[]
        def reject(uid):
            called.append(1)
            raise RuntimeError('private backend error')
        menu=InteractiveMenu(discover=lambda **kw:topology(),target_rpc=reject)
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
        menu=InteractiveMenu(discover=lambda **kw:topology(),target_rpc=slow)
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
        menu=InteractiveMenu(discover=lambda **kw:topology(),
                             target_rpc=lambda uid:sent.append(uid) or True)
        menu.event('encoder_sw','pressed',now=0)
        menu.state=menu.state.update(topology())
        menu.event('encoder_sw','pressed')
        menu.event('encoder','right')
        menu.event('encoder_sw','pressed')
        menu.state=replace(menu.state,focus=2)
        menu.event('encoder_sw','pressed')
        menu.event('encoder_sw','pressed')
        self.assertEqual(sent,[])
        self.assertEqual(menu.state.notice,'status.target_rejected')

    def test_no_acknowledgment_is_rejection(self):
        menu=InteractiveMenu(discover=lambda **kw:topology(),
                             target_rpc=lambda uid:False)
        menu_confirm(menu)
        menu._target_worker.join(timeout=2)
        menu.poll(now=0)
        self.assertEqual(menu.state.notice,'status.target_rejected')


if __name__=='__main__':
    unittest.main()
