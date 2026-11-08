"""M0.19 GPIO wake mapping: ordinary controls, no I/O."""

import unittest

from integrations.interfaces.interface_input import (
    should_publish_drive_activity,
)


class GPIOWakeMappingTests(unittest.TestCase):

    def test_normal_buttons_publish_activity(self):
        for name in ("stop", "skip", "previous"):
            with self.subTest(name=name):
                self.assertFalse(
                    should_publish_drive_activity(
                        name, "pressed", safe_executor=True
                    )
                )
                self.assertTrue(
                    should_publish_drive_activity(
                        name, "pressed", safe_executor=False
                    )
                )

    def test_encoder_movement_publishes_activity(self):
        for direction in ("left", "right"):
            with self.subTest(direction=direction):
                self.assertTrue(
                    should_publish_drive_activity(
                        "encoder", direction, safe_executor=True
                    )
                )

    def test_playpause_uses_own_wake_preflight(self):
        self.assertFalse(
            should_publish_drive_activity(
                "playpause", "pressed", safe_executor=True
            )
        )

    def test_releases_do_not_publish_activity(self):
        for name in ("playpause", "stop", "skip", "previous"):
            with self.subTest(name=name):
                self.assertFalse(
                    should_publish_drive_activity(
                        name, "released", safe_executor=True
                    )
                )

    def test_eject_uses_synchronous_wake(self):
        self.assertFalse(
            should_publish_drive_activity(
                "eject", "pressed", safe_executor=True
            )
        )
        self.assertTrue(
            should_publish_drive_activity(
                "eject", "pressed", safe_executor=False
            )
        )

    def test_every_configured_gpio_button_is_covered(self):
        from pathlib import Path
        from integrations.interfaces.input_core import load_config

        config = load_config(
            Path(__file__).resolve().parents[1]
            / "src/integrations/interfaces/interface.toml"
        )

        names = set(config.buttons) | {"encoder_sw"}
        self.assertIn("volume_up", names)
        self.assertIn("volume_dn", names)

        for name in sorted(names):
            with self.subTest(button=name):
                expected = name not in {
                    "playpause", "stop", "skip",
                    "previous", "eject",
                }
                self.assertEqual(
                    should_publish_drive_activity(
                        name, "pressed", safe_executor=True
                    ),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
