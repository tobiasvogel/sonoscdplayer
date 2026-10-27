"""M0.12b-r4 capability-probe / strategy-separation regressions."""
import unittest
from drive_power import DrivePowerManager
from tests.test_m012b_r3 import FakeRun, PAGE, CHANGEABLE


def cfg(strategy='auto', overrides=None):
    return {'enabled': True, 'strategy': strategy, 'interval': 10.0,
            'overrides': overrides or {}}


class ProbeTests(unittest.TestCase):
    def test_keepalive_still_probes_mode_page(self):
        fake = FakeRun(PAGE)
        manager = DrivePowerManager('/dev/sr0', cfg('keepalive'), runner=fake)
        manager.start()
        snap = manager.snapshot(reader_active=False)
        self.assertEqual(snap['active_strategy'], 'keepalive')
        self.assertEqual(snap['power_condition_probe'], 'supported')
        self.assertTrue(snap['supported']['power_condition_read'])
        self.assertFalse(snap['supported']['power_condition_write'])
        self.assertFalse(snap['power_condition_fields']['IACT']['changeable'])
        self.assertEqual([a[0] for a in fake.calls], ['sdparm', 'sg_start'])

    def test_auto_never_writes_without_overrides(self):
        fake = FakeRun(CHANGEABLE)
        manager = DrivePowerManager('/dev/sr0', cfg('auto'), runner=fake)
        manager.start()
        self.assertEqual(manager.active_strategy, 'keepalive')
        self.assertTrue(manager.supported['power_condition_write'])
        self.assertFalse(any('--set=' in part for a in fake.calls for part in a))

    def test_auto_writes_only_explicit_changeable_override(self):
        fake = FakeRun(CHANGEABLE)
        manager = DrivePowerManager('/dev/sr0', cfg('auto', {'IACT': 0}), runner=fake)
        manager.start()
        self.assertEqual(manager.active_strategy, 'power_condition')
        self.assertEqual(manager.power_condition_probe, 'supported')
        self.assertEqual(len([a for a in fake.calls if a[0] == 'sg_start']), 0)
        manager.close()

    def test_failed_probe_does_not_block_keepalive(self):
        fake = FakeRun()
        def run(args, **kw):
            if args[0] == 'sdparm':
                raise FileNotFoundError('sdparm unavailable')
            return fake(args, **kw)
        manager = DrivePowerManager('/dev/sr0', cfg('auto'), runner=run)
        manager.start()
        self.assertEqual(manager.active_strategy, 'keepalive')
        self.assertEqual(manager.power_condition_probe, 'failed')
        self.assertFalse(manager.supported['power_condition_read'])

    def test_snapshot_does_not_issue_drive_commands(self):
        fake = FakeRun()
        manager = DrivePowerManager('/dev/sr0', cfg('auto'), runner=fake)
        manager.start()
        before = len(fake.calls)
        for _ in range(10):
            manager.snapshot(reader_active=False)
        self.assertEqual(len(fake.calls), before)

    def test_reader_active_suspends_keepalive(self):
        fake = FakeRun()
        current = [0.0]
        manager = DrivePowerManager('/dev/sr0', cfg('keepalive'), runner=fake, clock=lambda: current[0])
        manager.start()
        current[0] = 30
        manager.tick(reader_active=True)
        self.assertEqual(len([a for a in fake.calls if a[0] == 'sg_start']), 1)
        manager.tick(reader_active=False)
        self.assertEqual(len([a for a in fake.calls if a[0] == 'sg_start']), 2)


if __name__ == '__main__':
    unittest.main()
