"""Unit tests of OLED shutdown marker and ordering."""
import tempfile
import unittest
from pathlib import Path
from integrations.outputs.oled_status import preserve_display_for_shutdown, preserve_display_on_exit

UNIT = Path(__file__).resolve().parents[1] / "systemd/sonoscdplayer-oled-shutdown-sentinel.service"

class ShutdownSentinelTests(unittest.TestCase):
    def test_default_close_is_not_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "flag"
            self.assertFalse(preserve_display_for_shutdown(environ={}, marker_path=marker))

    def test_marker_enables_preservation(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "flag"
            marker.touch()
            self.assertTrue(preserve_display_for_shutdown(environ={}, marker_path=marker))
            self.assertFalse(preserve_display_on_exit(environ={}))

    def test_manual_opt_in_still_supported(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "flag"
            self.assertTrue(preserve_display_for_shutdown(environ={"SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT":"1"}, marker_path=marker))

    def test_service_order(self):
        service = UNIT.read_text()
        self.assertIn("After=sonoscdplayer-oled.service", service)
        self.assertIn("ExecStop=/usr/bin/touch /run/sonoscdplayer-oled-shutdown-preserve", service)
        self.assertIn("RemainAfterExit=yes", service)
        self.assertIn("WantedBy=sonoscdplayer.target", service)

if __name__ == "__main__":
    unittest.main()
