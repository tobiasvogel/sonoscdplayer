"""M0.19 default idle configuration; strictly offline."""

import tempfile
import unittest
from pathlib import Path

from drive_power import load_config


ROOT = Path(__file__).resolve().parents[1]


class DefaultIdleConfigTests(unittest.TestCase):

    def load_text(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "player.toml"
            path.write_text(text, encoding="utf-8")
            return load_config(path)

    def test_shipped_default_is_120_seconds(self):
        config = load_config(ROOT / "src/config.toml")
        self.assertTrue(config["enabled"])
        self.assertEqual(config["strategy"], "auto")
        self.assertEqual(
            config["idle_spin_down_seconds"], 120.0
        )

    def test_custom_60_seconds(self):
        config = self.load_text(
            "[drive_power]\n"
            "enabled = true\n"
            'strategy = "auto"\n'
            "idle_spin_down_seconds = 60\n"
        )
        self.assertEqual(
            config["idle_spin_down_seconds"], 60.0
        )

    def test_legacy_config_remains_unchanged(self):
        config = self.load_text(
            "[drive_power]\n"
            "enabled = true\n"
            'strategy = "auto"\n'
        )
        self.assertIsNone(
            config["idle_spin_down_seconds"]
        )

    def test_less_than_30_seconds_rejected(self):
        with self.assertRaises(ValueError):
            self.load_text(
                "[drive_power]\n"
                "enabled = true\n"
                'strategy = "auto"\n'
                "idle_spin_down_seconds = 5\n"
            )


if __name__ == "__main__":
    unittest.main()
