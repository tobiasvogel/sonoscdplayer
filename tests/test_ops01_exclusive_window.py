"""OPS-01 offline sandbox-exclusive window; synthetic controllers only."""
import errno
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from tools.config_exclusive_window import OfflineExclusiveWindow, WindowRefused
from tools.config_guarded_worker import GuardedWorkerExecutor
from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)
from tools.config_maintenance_lifecycle import OfflineMaintenanceLifecycle
from tools.config_transaction_sandbox import OfflineSandbox
from tools.config_writer_lock import LockUnavailable, sandbox_writer_lock


ORIGINAL = b'[audio_startup]\nprebuffer_before_sonos=true\n'


class FakeReader:
    proc = None
    _reader_active = False

    def alive(self):
        return False

    def reader_active(self):
        return False


class FakeController:
    def __init__(self, mode="STOPPED"):
        self.state = SimpleNamespace(mode=SimpleNamespace(value=mode))
        self.owned_url = None
        self.player = None
        self.server = FakeReader()
        self._joined = {}
        self._group_uncertain = False
        self._recovery_url = None
        self._group_coord = None
        self._group_members = frozenset()


class ExclusiveWindowTests(unittest.TestCase):
    def make_window(self, sandbox, executor):
        barrier = OfflineMaintenanceBarrier()
        lifecycle = OfflineMaintenanceLifecycle(barrier, executor)
        return OfflineExclusiveWindow(sandbox, lifecycle), barrier

    def test_clean_window_aborts_and_resumes_original_worker(self):
        with OfflineSandbox() as sandbox, GuardedWorkerExecutor() as executor:
            sandbox.seed(ORIGINAL)
            window, barrier = self.make_window(sandbox, executor)
            before = sandbox.read_current()
            report = window.begin(FakeController())
            self.assertEqual(report["lifecycle_state"], "QUIESCED")
            self.assertTrue(report["sandbox_lock_held"])
            self.assertFalse(report["external_writers_excluded"])
            self.assertFalse(report["write_authorized"])
            ended = window.finish()
            self.assertEqual(ended["lifecycle_state"], "NORMAL")
            self.assertFalse(ended["sandbox_lock_held"])
            self.assertEqual(barrier.state, BarrierState.OPEN)
            self.assertEqual(barrier.submit(executor, lambda: 123).result(), 123)
            self.assertEqual(sandbox.read_current(), before)

    def test_lock_is_exclusive_during_window(self):
        with OfflineSandbox() as sandbox, GuardedWorkerExecutor() as executor:
            sandbox.seed(ORIGINAL)
            window, _ = self.make_window(sandbox, executor)
            window.begin(FakeController())
            try:
                with self.assertRaises(LockUnavailable):
                    with sandbox_writer_lock(sandbox._dirfd):
                        self.fail("second writer entered")
            finally:
                window.finish()
            with sandbox_writer_lock(sandbox._dirfd):
                pass

    def test_possible_change_never_reopens_barrier(self):
        with OfflineSandbox() as sandbox, GuardedWorkerExecutor() as executor:
            sandbox.seed(ORIGINAL)
            window, barrier = self.make_window(sandbox, executor)
            window.begin(FakeController())
            result = window.record_change_possible()
            self.assertEqual(result["lifecycle_state"], "CHANGE_POSSIBLE")
            self.assertFalse(result["write_authorized"])
            self.assertTrue(result["sandbox_lock_held"])
            result = window.finish()
            self.assertEqual(result["lifecycle_state"], "HALTED")
            self.assertEqual(barrier.state, BarrierState.CLOSED)
            self.assertFalse(result["sandbox_lock_held"])

    def test_changed_bytes_during_drain_reject_and_cleanly_cancel(self):
        with OfflineSandbox() as sandbox, GuardedWorkerExecutor() as executor:
            sandbox.seed(ORIGINAL)
            window, barrier = self.make_window(sandbox, executor)
            from tools import config_exclusive_window as module
            original_observe = module._observe
            calls = 0

            def edit_after_baseline(path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    Path(path).write_bytes(b'[audio_startup]\nprebuffer_before_sonos=false\n')
                return original_observe(path)

            with patch.object(module, "_observe", side_effect=edit_after_baseline):
                with self.assertRaises(WindowRefused):
                    window.begin(FakeController())
            self.assertEqual(barrier.state, BarrierState.OPEN)
            self.assertFalse(window.report()["sandbox_lock_held"])
            with sandbox_writer_lock(sandbox._dirfd):
                pass

    def test_change_after_lock_before_intent_rejects(self):
        with OfflineSandbox() as sandbox, GuardedWorkerExecutor() as executor:
            sandbox.seed(ORIGINAL)
            window, barrier = self.make_window(sandbox, executor)
            window.begin(FakeController())
            # Simulates a non-cooperating external editor bypassing flock.
            (sandbox.directory / "current.toml").write_bytes(b'x=2\n')
            with self.assertRaises(WindowRefused):
                window.record_change_possible()
            self.assertEqual(barrier.state, BarrierState.OPEN)
            self.assertFalse(window.report()["sandbox_lock_held"])

    def test_lock_contention_prevents_window_and_can_recover(self):
        with OfflineSandbox() as sandbox, GuardedWorkerExecutor() as executor:
            sandbox.seed(ORIGINAL)
            window, barrier = self.make_window(sandbox, executor)
            with sandbox_writer_lock(sandbox._dirfd):
                with self.assertRaises(LockUnavailable):
                    window.begin(FakeController())
            self.assertEqual(barrier.state, BarrierState.OPEN)
            self.assertFalse(window.report()["sandbox_lock_held"])

    def test_playing_controller_cannot_open_window(self):
        with OfflineSandbox() as sandbox, GuardedWorkerExecutor() as executor:
            sandbox.seed(ORIGINAL)
            window, barrier = self.make_window(sandbox, executor)
            with self.assertRaises(AdmissionRefused):
                window.begin(FakeController(mode="PLAYING"))
            self.assertEqual(barrier.state, BarrierState.CLOSED)
            self.assertFalse(window.report()["sandbox_lock_held"])

    def test_unseeded_sandbox_fails_before_drain(self):
        with OfflineSandbox() as sandbox, GuardedWorkerExecutor() as executor:
            window, barrier = self.make_window(sandbox, executor)
            with self.assertRaises(OSError):
                window.begin(FakeController())
            self.assertEqual(barrier.state, BarrierState.OPEN)
            self.assertFalse(window.report()["sandbox_lock_held"])

    def test_arbitrary_production_path_is_not_accepted(self):
        with GuardedWorkerExecutor() as executor:
            lifecycle = OfflineMaintenanceLifecycle(
                OfflineMaintenanceBarrier(), executor)
            with self.assertRaises(TypeError):
                OfflineExclusiveWindow(Path("/etc"), lifecycle)

    def test_never_grants_authorization_in_any_phase(self):
        with OfflineSandbox() as sandbox, GuardedWorkerExecutor() as executor:
            sandbox.seed(ORIGINAL)
            window, _ = self.make_window(sandbox, executor)
            for report in (window.report(), window.begin(FakeController()),
                           window.record_change_possible(), window.finish()):
                self.assertFalse(report["write_authorized"])
                self.assertFalse(report["production_maintenance_authorized"])
                self.assertFalse(report["service_restart_authorized"])
                self.assertFalse(report["external_writers_excluded"])


if __name__ == "__main__":
    unittest.main()
