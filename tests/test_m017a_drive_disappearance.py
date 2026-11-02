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
        with patch('drive_power.subprocess.run', side_effect=AssertionError('SCSI command called')):
            with self.assertRaisesRegex(RuntimeError, 'device disappeared'):
                manager._start_motor()

if __name__ == '__main__':
    unittest.main()
