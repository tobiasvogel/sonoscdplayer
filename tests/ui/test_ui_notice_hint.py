"""Persistent notices tell how to close them."""
import time
import unittest

from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
from sonoscdplayer.ui.notices.notice import Notice
from sonoscdplayer.ui.notices.render import NoticeRenderer


def frame_of(menu):
    seen = {}

    def capture(image):
        seen['image'] = image
        return image
    menu.frame(capture)
    return seen['image']


class DismissHintTests(unittest.TestCase):
    def test_hint_changes_only_the_bottom_area(self):
        notice = Notice('network.setup_requested', duration_seconds=0)
        for language in ('de', 'en'):
            plain = NoticeRenderer().render(notice, language=language)
            hinted = NoticeRenderer().render(notice, language=language, dismiss_hint=True)
            self.assertNotEqual(plain.tobytes(), hinted.tobytes())
            self.assertEqual(plain.crop((0, 0, 128, 40)).tobytes(),
                             hinted.crop((0, 0, 128, 40)).tobytes())

    def test_menu_shows_hint_only_for_notices_the_encoder_can_close(self):
        menu = InteractiveMenu(discover=lambda **_: None)
        menu.event('connect', 'setup_requested', now=time.monotonic())       # stays until pressed
        current = menu.connect_notices.current(now=time.monotonic())
        expected = NoticeRenderer().render(current, language=menu.language,
                                           grayscale=True, dismiss_hint=True)
        self.assertEqual(frame_of(menu).tobytes(), expected.tobytes())
        self.assertTrue(menu.event('encoder_sw', 'pressed', now=time.monotonic()))
        self.assertIsNone(menu.connect_notices.current(now=time.monotonic()))

    def test_no_hint_while_connect_is_held(self):
        menu = InteractiveMenu(discover=lambda **_: None)
        for action in ('settings_hint', 'warning_3'):
            menu.event('connect', action, now=time.monotonic())
            current = menu.connect_notices.current(now=time.monotonic())
            expected = NoticeRenderer().render(current, language=menu.language, grayscale=True)
            self.assertEqual(frame_of(menu).tobytes(), expected.tobytes())


if __name__ == '__main__':
    unittest.main()
