"""Offline tests for the M0.19 DiscMonitor idle gate."""

import unittest
from types import SimpleNamespace

from player_service import disc_poll_allowed


class DiscIdleGuardTests(unittest.TestCase):

    def make_power(self, **overrides):
        flags = dict(
            stop_attempted=False,
            spun_down=False,
            wake_in_progress=False,
            wake_fault_latched=False,
        )
        flags.update(overrides)
        return SimpleNamespace(
            idle_policy=SimpleNamespace(**flags)
        )

    def test_disabled_feature_preserves_monitor(self):
        self.assertTrue(disc_poll_allowed(None))
        self.assertTrue(disc_poll_allowed(
            SimpleNamespace(idle_policy=None)
        ))

    def test_normal_idle_monitor_remains_enabled(self):
        self.assertTrue(disc_poll_allowed(self.make_power()))

    def test_stop_attempt_blocks_media_probe(self):
        self.assertFalse(disc_poll_allowed(
            self.make_power(stop_attempted=True)
        ))

    def test_confirmed_spin_down_blocks_media_probe(self):
        self.assertFalse(disc_poll_allowed(
            self.make_power(spun_down=True)
        ))

    def test_wake_in_progress_blocks_media_probe(self):
        self.assertFalse(disc_poll_allowed(
            self.make_power(wake_in_progress=True)
        ))

    def test_wake_fault_blocks_media_probe(self):
        self.assertFalse(disc_poll_allowed(
            self.make_power(wake_fault_latched=True)
        ))

    def test_verified_wake_reenables_monitor(self):
        power = self.make_power(spun_down=True)
        self.assertFalse(disc_poll_allowed(power))
        power.idle_policy.spun_down = False
        self.assertTrue(disc_poll_allowed(power))


if __name__ == "__main__":
    unittest.main()
