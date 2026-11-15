"""USB drive reconnect: identity checks, observer, policy and coordination (simulated evidence)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from drive_identity import UsbDriveId, recovered_device, usb_identity
from player_service import restore_drive_for_explicit_action
from player_state import Mode
from wake_reconnect_policy import RecoveryState, assess_reconnect
from wake_reconnect_observer import (
    DeviceObservation,
    ObservationState,
    ReconnectObserver,
    ObservationResult,
)
from wake_reconnect_coordinator import CoordinationState, coordinate_reconnect
ORIGINAL = UsbDriveId("152d", "0583", "test-serial")
OTHER = UsbDriveId("152d", "0583", "other-serial")
A = DeviceObservation("/dev/sr0", ORIGINAL)
B = DeviceObservation("/dev/sr1", ORIGINAL)
FOREIGN = DeviceObservation("/dev/sr0", OTHER)



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


class ReconnectPolicyTests(unittest.TestCase):

    def assess(
        self,
        identities,
        *,
        disconnected=True,
        disc=True,
        original=ORIGINAL,
    ):
        return assess_reconnect(
            original_identity=original,
            observed_identities=identities,
            usb_disconnect_observed=disconnected,
            same_disc_verified=disc,
        )

    def test_original_device_and_disc_is_candidate(self):
        result = self.assess([ORIGINAL])
        self.assertEqual(
            result.state, RecoveryState.CANDIDATE
        )
        self.assertFalse(result.playback_authorized)

    def test_missing_device_blocked(self):
        self.assertEqual(
            self.assess([]).reason,
            "original_drive_missing",
        )

    def test_different_device_blocked(self):
        self.assertEqual(
            self.assess([OTHER]).reason,
            "original_drive_missing",
        )

    def test_ambiguous_device_blocked(self):
        self.assertEqual(
            self.assess([ORIGINAL, ORIGINAL]).reason,
            "original_drive_ambiguous",
        )

    def test_unverified_disc_blocked(self):
        self.assertEqual(
            self.assess([ORIGINAL], disc=False).reason,
            "original_disc_unverified",
        )

    def test_missing_disconnect_evidence_blocked(self):
        self.assertEqual(
            self.assess([ORIGINAL], disconnected=False).reason,
            "no_disconnect_observed",
        )

    def test_invalid_identity_blocked(self):
        self.assertEqual(
            self.assess([None]).reason,
            "observations_invalid",
        )

    def test_missing_original_identity_blocked(self):
        self.assertEqual(
            self.assess([ORIGINAL], original=None).reason,
            "original_identity_unavailable",
        )

    def test_no_result_authorizes_playback(self):
        cases = (
            self.assess([ORIGINAL]),
            self.assess([]),
            self.assess([OTHER]),
            self.assess([ORIGINAL], disc=False),
        )
        for result in cases:
            self.assertFalse(result.playback_authorized)


class ReconnectObserverTests(unittest.TestCase):

    def make(self):
        return ReconnectObserver(
            ORIGINAL, max_seconds=10.0
        )

    def test_original_drive_reappears(self):
        o = self.make()
        self.assertEqual(
            o.record(elapsed_seconds=0, devices=[A]).state,
            ObservationState.WAITING,
        )
        o.record(elapsed_seconds=1, devices=[])
        result = o.record(elapsed_seconds=2, devices=[A])
        self.assertEqual(result.state, ObservationState.REAPPEARED)
        self.assertFalse(result.playback_authorized)

    def test_device_renumbering(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[A])
        o.record(elapsed_seconds=1, devices=[])
        result = o.record(elapsed_seconds=2, devices=[B])
        self.assertEqual(result.state, ObservationState.REAPPEARED)
        self.assertEqual(result.device, "/dev/sr1")

    def test_no_disconnect_no_recovery(self):
        o = self.make()
        for t in (0, 1, 2):
            result = o.record(elapsed_seconds=t, devices=[A])
        self.assertEqual(result.state, ObservationState.WAITING)

    def test_foreign_drive_never_satisfies_recovery(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[])
        result = o.record(
            elapsed_seconds=2, devices=[FOREIGN]
        )
        self.assertEqual(result.state, ObservationState.WAITING)

    def test_ambiguous_original_identity_blocks(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[])
        result = o.record(elapsed_seconds=2, devices=[A, B])
        self.assertEqual(result.state, ObservationState.BLOCKED)

    def test_timeout_blocks(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[])
        result = o.record(elapsed_seconds=11, devices=[A])
        self.assertEqual(result.state, ObservationState.BLOCKED)

    def test_incomplete_inventory_blocks(self):
        o = self.make()
        result = o.record(elapsed_seconds=0, devices=None)
        self.assertEqual(result.state, ObservationState.BLOCKED)

    def test_time_reversal_blocks(self):
        o = self.make()
        o.record(elapsed_seconds=3, devices=[])
        result = o.record(elapsed_seconds=2, devices=[A])
        self.assertEqual(result.state, ObservationState.BLOCKED)

    def test_terminal_result_is_latched(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[A])
        o.record(elapsed_seconds=1, devices=[])
        first = o.record(elapsed_seconds=2, devices=[A])
        second = o.record(elapsed_seconds=3, devices=[FOREIGN])
        self.assertEqual(first, second)

    def test_initial_absence_does_not_prove_disconnect(self):
        o = self.make()
        first = o.record(elapsed_seconds=0, devices=[])
        second = o.record(elapsed_seconds=1, devices=[A])

        self.assertEqual(first.state, ObservationState.WAITING)
        self.assertEqual(
            first.reason, "awaiting_original_presence"
        )
        self.assertEqual(second.state, ObservationState.WAITING)
        self.assertEqual(second.reason, "awaiting_disconnect")

    def test_initial_foreign_drive_does_not_prove_disconnect(self):
        o = self.make()
        o.record(elapsed_seconds=0, devices=[FOREIGN])
        result = o.record(elapsed_seconds=1, devices=[A])
        self.assertEqual(result.state, ObservationState.WAITING)

    def test_nonfinite_time_blocks(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                o = self.make()
                result = o.record(
                    elapsed_seconds=value, devices=[A]
                )
                self.assertEqual(
                    result.state, ObservationState.BLOCKED
                )

    def test_no_observation_authorizes_playback(self):
        o = self.make()
        results = [
            o.record(elapsed_seconds=0, devices=[]),
            o.record(elapsed_seconds=1, devices=[A]),
        ]
        self.assertTrue(
            all(not r.playback_authorized for r in results)
        )


def completed_observation(*, renumber=False):
    observer = ReconnectObserver(
        ORIGINAL, max_seconds=10
    )
    observer.record(elapsed_seconds=0, devices=[A])
    observer.record(elapsed_seconds=1, devices=[])
    return observer.record(
        elapsed_seconds=2,
        devices=[B if renumber else A],
    )


class ReconnectCoordinatorTests(unittest.TestCase):

    def assess(
        self,
        *,
        observation=None,
        devices=None,
        disc=True,
        transport=True,
        fault=True,
    ):
        if observation is None:
            observation = completed_observation()
        if devices is None:
            devices = [A]
        return coordinate_reconnect(
            original_identity=ORIGINAL,
            observation=observation,
            current_devices=devices,
            same_disc_verified=disc,
            start_transport_failed=transport,
            wake_fault_latched=fault,
        )

    def test_complete_evidence_is_not_authorization(self):
        result = self.assess()
        self.assertEqual(
            result.state,
            CoordinationState.EVIDENCE_COMPLETE,
        )
        self.assertEqual(result.device, "/dev/sr0")
        self.assertFalse(result.playback_authorized)
        self.assertFalse(result.wake_fault_clearable)
        self.assertFalse(result.motor_command_authorized)

    def test_renumbered_original_drive(self):
        result = self.assess(
            observation=completed_observation(
                renumber=True
            ),
            devices=[B],
        )
        self.assertEqual(
            result.state,
            CoordinationState.EVIDENCE_COMPLETE,
        )
        self.assertEqual(result.device, "/dev/sr1")

    def test_no_transport_error_blocks(self):
        result = self.assess(transport=False)
        self.assertEqual(
            result.reason,
            "no_start_transport_failure",
        )

    def test_unlatched_fault_blocks(self):
        result = self.assess(fault=False)
        self.assertEqual(
            result.reason,
            "wake_fault_not_latched",
        )

    def test_unverified_disc_blocks(self):
        result = self.assess(disc=False)
        self.assertEqual(
            result.reason,
            "original_disc_unverified",
        )

    def test_foreign_device_blocks(self):
        result = self.assess(devices=[FOREIGN])
        self.assertEqual(
            result.state,
            CoordinationState.BLOCKED,
        )

    def test_ambiguous_identity_blocks(self):
        result = self.assess(devices=[A, B])
        self.assertEqual(
            result.reason,
            "original_identity_not_unique",
        )

    def test_device_path_mismatch_blocks(self):
        result = self.assess(devices=[B])
        self.assertEqual(
            result.reason,
            "reappeared_device_path_mismatch",
        )

    def test_no_disconnect_blocks(self):
        observer = ReconnectObserver(ORIGINAL)
        waiting = observer.record(
            elapsed_seconds=0, devices=[A]
        )
        result = self.assess(observation=waiting)
        self.assertEqual(
            result.reason,
            "reconnect_not_proven",
        )

    def test_timeout_blocks(self):
        observer = ReconnectObserver(
            ORIGINAL, max_seconds=10
        )
        observer.record(
            elapsed_seconds=0, devices=[A]
        )
        timed_out = observer.record(
            elapsed_seconds=11, devices=[]
        )
        result = self.assess(
            observation=timed_out
        )
        self.assertEqual(
            result.state,
            CoordinationState.BLOCKED,
        )

    def test_duplicate_device_paths_block(self):
        result = self.assess(
            devices=[A, A]
        )
        self.assertEqual(
            result.reason,
            "duplicate_device_path",
        )

    def test_invalid_inventory_blocks(self):
        result = self.assess(devices=[None])
        self.assertEqual(
            result.reason,
            "invalid_device_inventory",
        )

    def test_all_results_keep_safety_barriers(self):
        for result in (
            self.assess(),
            self.assess(disc=False),
            self.assess(fault=False),
            self.assess(devices=[FOREIGN]),
        ):
            self.assertFalse(result.playback_authorized)
            self.assertFalse(result.wake_fault_clearable)
            self.assertFalse(result.motor_command_authorized)


if __name__ == '__main__':
    unittest.main()
