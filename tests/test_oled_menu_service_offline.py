"""An open on-device menu must stay visible while the player service is down."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from integrations.outputs import oled_status
from integrations.outputs.ssd1327 import Framebuffer


class FakeMenu:
    def __init__(self, *, active, **_kwargs):
        self.language = 'en'
        self.active = active
        self.frame_calls = 0

    def poll(self):
        pass

    def event(self, *_args):
        pass

    def frame(self, _image_to_frame):
        self.frame_calls += 1
        return Framebuffer()


class FakeReceiver:
    def __init__(self, _path):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def receive(self):
        return None


class MenuWhileServiceOfflineTests(unittest.TestCase):
    def run_offline(self, *, menu_active):
        menus = []

        def make_menu(**kwargs):
            menus.append(FakeMenu(active=menu_active, **kwargs))
            return menus[-1]

        with tempfile.TemporaryDirectory() as tmp, \
                patch('sonos_ui.interactive_menu.InteractiveMenu', make_menu), \
                patch('sonos_ui.ui_event_bridge.EventReceiver', FakeReceiver), \
                redirect_stdout(io.StringIO()) as out:
            oled_status.main([
                '--ui-modern',
                '--ui-events-socket', str(Path(tmp) / 'events.sock'),
                '--socket', str(Path(tmp) / 'missing-player.sock'),
                '--seconds', '0.02', '--splash-seconds', '0', '--refresh-ms', '200'])
        return menus[0], out.getvalue()

    def test_open_menu_is_drawn_when_service_is_offline(self):
        menu, output = self.run_offline(menu_active=True)
        self.assertGreaterEqual(menu.frame_calls, 1)
        self.assertIn('status_unavailable', output)

    def test_error_screen_is_used_when_menu_is_closed(self):
        menu, output = self.run_offline(menu_active=False)
        self.assertEqual(menu.frame_calls, 0)
        self.assertIn('status_unavailable', output)


if __name__ == '__main__':
    unittest.main()
