"""M0.19 EJECT idle-state guard; no hardware operations."""
import unittest
from types import SimpleNamespace

from player_service import eject_blocked_by_idle_policy


class EjectIdlePolicyTests(unittest.TestCase):

    def power(self, **flags):
        defaults = {
            'stop_attempted': False,
            'spun_down': False,
            'wake_in_progress': False,
            'wake_fault_latched': False,
        }
        defaults.update(flags)
        return SimpleNamespace(
            idle_policy=SimpleNamespace(**defaults)
        )

    def test_disabled_feature_preserves_inp03(self):
        self.assertFalse(eject_blocked_by_idle_policy(None))
        self.assertFalse(eject_blocked_by_idle_policy(
            SimpleNamespace(idle_policy=None)
        ))

    def test_normal_awake_drive_can_use_inp03(self):
        self.assertFalse(
            eject_blocked_by_idle_policy(self.power())
        )

    def test_unconfirmed_stop_keeps_the_disc_removable(self):
        self.assertFalse(eject_blocked_by_idle_policy(
            self.power(stop_attempted=True)))

    def test_unconfirmed_stop_with_wake_in_progress_still_blocks(self):
        self.assertTrue(eject_blocked_by_idle_policy(
            self.power(stop_attempted=True, wake_in_progress=True)))

    def test_spun_down_drive_blocks_eject(self):
        # An acknowledged STOP is healthy: the button path wakes the drive first.
        for extra in ({}, {'stop_attempted': True}):
            with self.subTest(extra=extra):
                self.assertTrue(eject_blocked_by_idle_policy(
                    self.power(spun_down=True, **extra)))

    def test_wake_in_progress_blocks_eject(self):
        self.assertTrue(
            eject_blocked_by_idle_policy(
                self.power(wake_in_progress=True)
            )
        )

    def test_wake_fault_keeps_the_disc_removable(self):
        # A failed wake leaves the drive marked spun down; EJECT stays possible
        # because the eject path verifies drive and media itself.
        for extra in ({}, {'spun_down': True}, {'spun_down': True, 'stop_attempted': True}):
            with self.subTest(extra=extra):
                self.assertFalse(eject_blocked_by_idle_policy(
                    self.power(wake_fault_latched=True, **extra)))

    def test_wake_in_progress_blocks_even_with_a_latched_fault(self):
        self.assertTrue(eject_blocked_by_idle_policy(
            self.power(wake_fault_latched=True, wake_in_progress=True)))


if __name__ == "__main__":
    unittest.main()
