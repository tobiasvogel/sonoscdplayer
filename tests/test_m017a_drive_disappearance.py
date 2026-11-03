"""Missing optical device must not trigger SCSI motor commands."""
import unittest
from unittest.mock import patch
from drive_power import DrivePowerManager


class DriveDisappearanceTests(unittest.TestCase):
    def test_missing_device_skips_sg_start(self):
        manager = DrivePowerManager('/dev/sr987654', {
            'enabled': True, 'strategy': 'keepalive',
            'interval': 10.0, 'overrides': {},
        })
        with patch.object(manager, '_command', side_effect=AssertionError('SCSI command called')):
            with self.assertRaisesRegex(RuntimeError, 'device disappeared'):
                manager._start_motor()

    def test_rebind_preserves_keepalive_and_rearms_polling(self):
        clock = lambda: 100.0
        manager = DrivePowerManager('/dev/sr0', {
            'enabled': True, 'strategy': 'keepalive',
            'interval': 10.0, 'overrides': {},
        }, clock=clock)
        manager.active_strategy = 'keepalive'
        manager.last_start = 99.0
        with patch('drive_power.Path.exists', return_value=True):
            manager.rebind_device('/dev/sr1')
        self.assertEqual(manager.device, '/dev/sr1')
        self.assertEqual(manager.active_strategy, 'keepalive')
        self.assertEqual(manager.config['interval'], 10.0)
        self.assertEqual(manager.last_start, 90.0)

    def test_rebind_refuses_active_reader(self):
        manager = DrivePowerManager('/dev/sr0', {
            'enabled': True, 'strategy': 'keepalive',
            'interval': 10.0, 'overrides': {},
        })
        with self.assertRaises(RuntimeError):
            manager.rebind_device('/dev/sr1', reader_active=True)
        self.assertEqual(manager.device, '/dev/sr0')

    def test_rebind_refuses_missing_or_unexpected_device(self):
        manager = DrivePowerManager('/dev/sr0', {
            'enabled': True, 'strategy': 'keepalive',
            'interval': 10.0, 'overrides': {},
        })
        with self.assertRaises(ValueError):
            manager.rebind_device('/tmp/not-a-drive')
        with patch('drive_power.Path.exists', return_value=False):
            with self.assertRaises(RuntimeError):
                manager.rebind_device('/dev/sr1')
        self.assertEqual(manager.device, '/dev/sr0')


if __name__ == '__main__':
    unittest.main()
