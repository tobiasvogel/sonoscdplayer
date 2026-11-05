"""Non-activating CLI checks for the guided one-boot exitrd script."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/manage_experimental_exitrd.sh"


class GuidedExitrdTests(unittest.TestCase):
    def call(self, *args, stdin=None, env=None):
        return subprocess.run(
            ["sh", str(SCRIPT), *args],
            input=stdin, text=True, capture_output=True,
            env={**os.environ, **(env or {})}, timeout=10)

    def test_help_is_read_only(self):
        result = self.call("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--enable-once", result.stdout)
        self.assertIn("--disable", result.stdout)
        self.assertIn("AT YOUR OWN RISK", SCRIPT.read_text())

    def test_invalid_calls_rejected_before_build(self):
        for args in (("--enable-once", "--config"),
                     ("--status", "--config", ""),
                     ("--unknown",)):
            with self.subTest(args=args):
                self.assertEqual(self.call(*args).returncode, 2)

    def test_status_on_actual_player_config_is_read_only(self):
        result = self.call("--status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("experimental_safe_screen=", result.stdout)
        self.assertIn("READ-ONLY", result.stdout)

    def test_status_on_default_example_is_read_only(self):
        result = self.call("--status", "--config", str(ROOT / "config/player.example.toml"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("experimental_safe_screen=false", result.stdout)
        self.assertIn("RAM exitrd mount:", result.stdout)

    def test_disabled_config_blocks_noninteractive_enable_without_build(self):
        result = self.call("--enable-once", "--config",
                           str(ROOT / "config/player.example.toml"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("experimental_safe_screen = true", result.stderr)

    def test_enabled_config_needs_interactive_terminal_to_activate(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / "player.toml"
            cfg.write_text("[service]\nexperimental_safe_screen = true\n")
            result = self.call("--enable-once", "--config", str(cfg),
                               stdin="YES-I-ACCEPT\n")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("interactive terminal required", result.stderr)
            self.assertFalse((Path(directory) / "shutdown").exists())

    def test_source_requires_two_acknowledgements_before_staging(self):
        source = SCRIPT.read_text()
        self.assertIn("YES-I-ACCEPT", source)
        self.assertIn("EXITRD_UNVERIFIED_SAFE=1", source)
        self.assertIn("I_ACCEPT_EARLY_SAFE_SCREEN_RISK", source)
        self.assertIn("I_UNDERSTAND_EXITRD_CAN_PREVENT_CLEAN_SHUTDOWN", source)
        self.assertIn("--expected-sha256", source)
        self.assertNotIn("systemctl poweroff", source)
        self.assertNotIn("systemctl enable", source)


if __name__ == "__main__":
    unittest.main()
