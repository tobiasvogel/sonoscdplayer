"""OPS-01 isolated command barrier regression checks."""
import dataclasses
import threading
import unittest

from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, IdleProof, OfflineMaintenanceBarrier,
)

PROOF = IdleProof(True, True, True, True, True, True, True, True)


class MaintenanceBarrierTests(unittest.TestCase):
    def test_open_never_authorizes_config_writes(self):
        gate = OfflineMaintenanceBarrier()
        self.assertEqual(gate.state, BarrierState.OPEN)
        self.assertFalse(gate.report()["write_authorized"])

    def test_drain_rejects_new_work(self):
        gate = OfflineMaintenanceBarrier()
        gate.begin_draining()
        with self.assertRaises(AdmissionRefused):
            gate.admit()
        self.assertEqual(gate.in_flight, 0)

    def test_queued_command_prevents_freeze_until_finished(self):
        gate = OfflineMaintenanceBarrier()
        ticket = gate.admit()
        gate.begin_draining()
        with self.assertRaises(AdmissionRefused):
            gate.freeze(PROOF)
        gate.finish(ticket)
        gate.freeze(PROOF)
        self.assertEqual(gate.state, BarrierState.FROZEN)

    def test_pending_refresh_prevents_freeze(self):
        gate = OfflineMaintenanceBarrier()
        refresh = gate.admit()
        gate.begin_draining()
        with self.assertRaises(AdmissionRefused):
            gate.freeze(PROOF)
        gate.finish(refresh)
        gate.freeze(PROOF)

    def test_missing_proof_rejected(self):
        gate = OfflineMaintenanceBarrier()
        gate.begin_draining()
        with self.assertRaises(AdmissionRefused):
            gate.freeze(None)

    def test_every_idle_evidence_field_is_mandatory(self):
        for field in dataclasses.asdict(PROOF):
            with self.subTest(field=field):
                gate = OfflineMaintenanceBarrier()
                gate.begin_draining()
                with self.assertRaises(AdmissionRefused):
                    gate.freeze(dataclasses.replace(PROOF, **{field: False}))

    def test_nonboolean_proof_rejected(self):
        gate = OfflineMaintenanceBarrier()
        gate.begin_draining()
        with self.assertRaises(AdmissionRefused):
            gate.freeze(dataclasses.replace(PROOF, no_reader=1))

    def test_read_only_diagnostic_does_not_freeze(self):
        gate = OfflineMaintenanceBarrier()
        gate.begin_draining()
        self.assertIsNone(gate.admit(read_only=True))
        self.assertEqual(gate.state, BarrierState.DRAINING)
        self.assertFalse(gate.report()["write_authorized"])

    def test_close_is_terminal(self):
        gate = OfflineMaintenanceBarrier()
        gate.admit()
        gate.close()
        self.assertEqual(gate.state, BarrierState.CLOSED)
        with self.assertRaises(AdmissionRefused):
            gate.begin_draining()
        with self.assertRaises(AdmissionRefused):
            gate.admit()

    def test_simultaneous_submission_and_drain(self):
        for _ in range(12):
            gate = OfflineMaintenanceBarrier()
            ready = threading.Event()
            accepted = []

            def submit():
                ready.wait()
                try:
                    accepted.append(gate.admit())
                except AdmissionRefused:
                    return

            thread = threading.Thread(target=submit)
            thread.start()
            ready.set()
            gate.begin_draining()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            with self.assertRaises(AdmissionRefused):
                gate.admit()
            for ticket in accepted:
                gate.finish(ticket)
            gate.freeze(PROOF)
            self.assertEqual(gate.state, BarrierState.FROZEN)


if __name__ == "__main__":
    unittest.main()
