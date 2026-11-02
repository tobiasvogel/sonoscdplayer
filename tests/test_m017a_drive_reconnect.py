"""Offline USB reconnect acceptance without Sonos writes or keepalive changes."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from drive_identity import UsbDriveId, recovered_device, usb_identity
from player_service import restore_drive_for_explicit_action
from player_state import Mode


class ReconnectTests(unittest.TestCase):
    def test_usb_serial_identity_survives_sr_renumber(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bus = root / 'bus'
            bus.mkdir()
            for key, value in [('idVendor','0e8d'),('idProduct','1836'),('serial','RC6U504')]:
                (bus/key).write_text(value)
            for name in ('sr0','sr1'):
                link = root / name / 'device'
                link.parent.mkdir()
                link.symlink_to(bus, target_is_directory=True)
            expected = UsbDriveId('0e8d','1836','RC6U504')
            self.assertEqual(usb_identity('/dev/sr0',sys_root=root), expected)
            self.assertEqual(usb_identity('/dev/sr1',sys_root=root), expected)

    def test_ambiguous_or_missing_drive_is_rejected(self):
        original = UsbDriveId('0e8d','1836','RC6U504')
        with patch('drive_identity.Path.exists',return_value=True), patch(
                'drive_identity.usb_identity',return_value=original):
            with self.assertRaisesRegex(RuntimeError,'not uniquely'):
                recovered_device(original,list_nodes=lambda:['sr0','sr1'])
        with self.assertRaises(RuntimeError):
            recovered_device(None,list_nodes=lambda:[])

    def test_rebind_updates_both_consumers_and_keeps_cadence(self):
        original = UsbDriveId('0e8d','1836','RC6U504')
        server = NS(device='/dev/sr0',alive=lambda:False,reader_active=lambda:False)
        controller = NS(state=NS(mode=Mode.STOPPED),owned_url=None,server=server,_joined={})
        power = NS(device='/dev/sr0',active_strategy='keepalive',
                   config={'interval':10},rebind_device=lambda dev,reader_active=False:setattr(power,'device',dev))
        with patch('player_service.Path.exists',return_value=False), patch(
                'player_service.recovered_device',return_value='/dev/sr1'), patch(
                'player_service.usb_identity',return_value=original), patch(
                'player_service.read_toc',return_value='same toc'):
            self.assertTrue(restore_drive_for_explicit_action(controller,power,original,'same toc'))
        self.assertEqual((controller.server.device,power.device),('/dev/sr1','/dev/sr1'))
        self.assertEqual(power.active_strategy,'keepalive')
        self.assertEqual(power.config['interval'],10)

    def test_different_disc_rejected_before_rebind(self):
        original = UsbDriveId('0e8d','1836','RC6U504')
        server = NS(device='/dev/sr0',alive=lambda:False,reader_active=lambda:False)
        controller = NS(state=NS(mode=Mode.ERROR),owned_url=None,server=server,_joined={})
        power = NS(rebind_device=lambda *a,**kw:self.fail('rebind called'))
        with patch('player_service.Path.exists',return_value=False), patch(
                'player_service.recovered_device',return_value='/dev/sr1'), patch(
                'player_service.usb_identity',return_value=original), patch(
                'player_service.read_toc',return_value='different'):
            with self.assertRaisesRegex(RuntimeError,'different or unreadable disc'):
                restore_drive_for_explicit_action(controller,power,original,'original')
        self.assertEqual(server.device,'/dev/sr0')

    def test_unresolved_group_recovery_blocks_rebind(self):
        server = NS(device='/dev/sr0', alive=lambda: False,
                    reader_active=lambda: False)
        controller = NS(state=NS(mode=Mode.ERROR), owned_url=None,
                        server=server, _joined={}, _group_uncertain=True)
        with patch('player_service.recovered_device',
                   side_effect=AssertionError('recovery must not run')):
            self.assertFalse(restore_drive_for_explicit_action(
                controller, None, UsbDriveId('0e8d', '1836', 'RC6U504'), 'toc'))

    def test_active_reader_blocks_rebind(self):
        server=NS(device='/dev/sr0',alive=lambda:True,reader_active=lambda:True)
        controller=NS(state=NS(mode=Mode.PLAYING),owned_url='owned',server=server,_joined={})
        self.assertFalse(restore_drive_for_explicit_action(controller,None,None,'toc'))


if __name__=='__main__':
    unittest.main()
