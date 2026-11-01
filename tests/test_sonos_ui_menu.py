"""Offline tests for opt-in OLED encoder/menu coordinator."""
import unittest
from types import SimpleNamespace as NS
from sonos_ui.interactive_menu import InteractiveMenu
from sonos_ui.multi_target import from_devices


def topology():
    a=NS(uid='one',player_name='Büro')
    b=NS(uid='two',player_name='Wohnzimmer')
    g=NS(coordinator=a,members=(a,b))
    a.group=b.group=g
    return from_devices((a,b))


class InteractiveMenuTests(unittest.TestCase):
    def make_menu(self):
        return InteractiveMenu(discover=lambda *,timeout: topology())

    def test_closed_menu_ignores_encoder(self):
        menu=self.make_menu()
        self.assertFalse(menu.event('encoder','right'))
        self.assertFalse(menu.active)

    def test_encoder_opens_and_back_closes_menu(self):
        menu=self.make_menu()
        self.assertTrue(menu.event('encoder_sw','pressed',now=0))
        self.assertTrue(menu.active)
        menu.state=menu.state.update(topology())
        menu.state=__import__('dataclasses').replace(menu.state,focus=len(menu.state.topology.targets)+2)
        self.assertTrue(menu.event('encoder_sw','pressed',now=1))
        self.assertFalse(menu.active)

    def test_encoder_selects_only_local_draft(self):
        menu=self.make_menu()
        menu.event('encoder_sw','pressed',now=0)
        menu.state=menu.state.update(topology())
        self.assertTrue(menu.event('encoder_sw','pressed'))
        self.assertEqual(len(menu.state.draft),1)
        self.assertFalse(menu.state.applied)

    def test_encoder_moves_focus(self):
        menu=self.make_menu()
        menu.event('encoder_sw','pressed',now=0)
        menu.state=menu.state.update(topology())
        menu.event('encoder','right')
        self.assertEqual(menu.state.focus,1)

    def test_back_cancels_unconfirmed_selection(self):
        menu=self.make_menu()
        menu.event('encoder_sw','pressed',now=0)
        menu.state=menu.state.update(topology())
        menu.event('encoder_sw','pressed')
        menu.state=__import__('dataclasses').replace(menu.state,focus=len(menu.state.topology.targets)+2)
        menu.event('encoder_sw','pressed',now=1)
        self.assertFalse(menu.active)
        self.assertEqual(menu.state.draft,menu.state.applied)

    def test_release_event_is_ignored(self):
        menu=self.make_menu()
        menu.event('encoder_sw','pressed',now=0)
        self.assertFalse(menu.event('encoder_sw','released'))

    def test_render_valid_frame(self):
        from integrations.outputs.oled_status import image_to_frame
        menu=self.make_menu()
        menu.event('encoder_sw','pressed',now=0)
        menu.state=menu.state.update(topology())
        frame=menu.frame(image_to_frame)
        self.assertEqual(len(frame.data),8192)


if __name__=='__main__':
    unittest.main()
