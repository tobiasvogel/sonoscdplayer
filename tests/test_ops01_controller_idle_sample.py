"""Pure controller-memory samples: no live player, drives, or Sonos."""
import types
import unittest

from tools.config_controller_idle import sample_controller_idle
from tools.config_maintenance_barrier import OfflineMaintenanceBarrier, AdmissionRefused


class FakeServer:
    proc = None
    _reader_active = False

    def alive(self):
        return False

    def reader_active(self):
        return False


def controller():
    return types.SimpleNamespace(
        state=types.SimpleNamespace(mode=types.SimpleNamespace(value="STOPPED")),
        owned_url=None,
        player=None,
        server=FakeServer(),
        _joined={},
        _group_uncertain=False,
        _recovery_url=None,
        _group_coord=None,
        _group_members=frozenset(),
    )


class ControllerIdleSampleTests(unittest.TestCase):
    def test_idle_is_only_partial_evidence(self):
        proof = sample_controller_idle(controller())
        self.assertTrue(proof.mode_stopped)
        self.assertTrue(proof.no_owned_url)
        self.assertTrue(proof.no_stream)
        self.assertTrue(proof.no_reader)
        self.assertTrue(proof.no_joined_group)
        self.assertTrue(proof.no_uncertain_group)
        self.assertTrue(proof.no_group_recovery)
        self.assertFalse(proof.no_pending_external_activity)

    def test_partial_evidence_can_never_freeze_gate(self):
        gate = OfflineMaintenanceBarrier()
        gate.begin_draining()
        with self.assertRaises(AdmissionRefused):
            gate.freeze(sample_controller_idle(controller()))
        self.assertFalse(gate.report()["write_authorized"])

    def test_joined_group_blocks_even_without_cleanup_pending(self):
        ctrl = controller()
        ctrl._joined = {"member": object()}
        ctrl._group_uncertain = False
        self.assertFalse(sample_controller_idle(ctrl).no_joined_group)

    def test_stale_reader_flag_blocks_even_after_child_exits(self):
        ctrl = controller()
        ctrl.server._reader_active = True
        self.assertFalse(sample_controller_idle(ctrl).no_reader)

    def test_live_child_blocks(self):
        ctrl = controller()
        ctrl.server.proc = object()
        self.assertFalse(sample_controller_idle(ctrl).no_stream)

    def test_ownership_and_sonos_attachment_block(self):
        for name, value in (("owned_url", "synthetic"), ("player", object())):
            with self.subTest(name=name):
                ctrl = controller()
                setattr(ctrl, name, value)
                self.assertFalse(sample_controller_idle(ctrl).no_owned_url)

    def test_hidden_group_recovery_blocks(self):
        for name, value in (
            ("_recovery_url", "synthetic"),
            ("_group_coord", object()),
            ("_group_members", frozenset({"synthetic"})),
            ("_group_uncertain", True),
        ):
            with self.subTest(name=name):
                ctrl = controller()
                setattr(ctrl, name, value)
                proof = sample_controller_idle(ctrl)
                self.assertFalse(proof.no_group_recovery and proof.no_uncertain_group)

    def test_missing_evidence_blocks_every_field(self):
        ctrl = controller()
        del ctrl.server._reader_active
        self.assertFalse(sample_controller_idle(ctrl).no_reader)
        del ctrl._joined
        proof = sample_controller_idle(ctrl)
        self.assertFalse(proof.no_joined_group)
        self.assertFalse(proof.no_pending_external_activity)

    def test_unknown_mode_is_not_stopped(self):
        ctrl = controller()
        ctrl.state.mode.value = "ERROR"
        self.assertFalse(sample_controller_idle(ctrl).mode_stopped)

    def test_process_inspection_failure_blocks(self):
        ctrl = controller()
        def fail():
            raise OSError("synthetic unavailable")
        ctrl.server.alive = fail
        proof = sample_controller_idle(ctrl)
        self.assertFalse(proof.no_stream)
        self.assertFalse(proof.no_reader)


if __name__ == "__main__":
    unittest.main()
