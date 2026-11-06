"""OPS-01 opt-in socket worker admission; no service or hardware operations."""
import concurrent.futures
import threading
import unittest

from player_service import admit_maintenance_work, submit_maintenance_work
from tools.config_maintenance_barrier import (
    AdmissionRefused, BarrierState, OfflineMaintenanceBarrier,
)


class MaintenanceWorkerAdmissionTests(unittest.TestCase):
    def test_disabled_default_preserves_unrestricted_admission(self):
        self.assertIsNone(admit_maintenance_work(None, {'command': 'play'}))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(submit_maintenance_work(pool, None, lambda: 4).result(), 4)

    def test_only_exact_status_is_diagnostic(self):
        barrier = OfflineMaintenanceBarrier()
        barrier.begin_draining()
        self.assertIsNone(admit_maintenance_work(barrier, {'command': 'status'}))
        for request in ({'command': 'status', 'read_only': True},
                        {'command': 'play', 'read_only': True},
                        {'command': 'help'}, {'command': 'sonos-status'},
                        {'command': 'status', 'uid': 'ignored'}, None):
            with self.subTest(request=request):
                with self.assertRaises(AdmissionRefused):
                    admit_maintenance_work(barrier, request)

    def test_queued_and_running_work_are_counted(self):
        barrier = OfflineMaintenanceBarrier()
        started = threading.Event()
        release = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def first():
                started.set()
                release.wait(timeout=5)
            running = submit_maintenance_work(pool, barrier, first)
            self.assertTrue(started.wait(timeout=5))
            queued = submit_maintenance_work(pool, barrier, lambda: 7)
            self.assertEqual(barrier.in_flight, 2)
            barrier.begin_draining()
            with self.assertRaises(AdmissionRefused):
                submit_maintenance_work(pool, barrier, lambda: 8)
            release.set()
            running.result(timeout=5)
            self.assertEqual(queued.result(timeout=5), 7)
            self.assertEqual(barrier.in_flight, 0)

    def test_cancelled_queued_work_releases_ticket(self):
        barrier = OfflineMaintenanceBarrier()
        started = threading.Event()
        release = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def blocking():
                started.set()
                release.wait(timeout=5)
            running = submit_maintenance_work(pool, barrier, blocking)
            self.assertTrue(started.wait(timeout=5))
            queued = submit_maintenance_work(pool, barrier, lambda: 7)
            barrier.begin_draining()
            self.assertTrue(queued.cancel())
            self.assertEqual(barrier.in_flight, 1)
            release.set()
            running.result(timeout=5)
        self.assertEqual(barrier.in_flight, 0)

    def test_terminal_close_during_running_work_is_harmless(self):
        barrier = OfflineMaintenanceBarrier()
        started = threading.Event()
        release = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def running():
                started.set()
                self.assertTrue(release.wait(timeout=5))
                return 9
            future = submit_maintenance_work(pool, barrier, running)
            self.assertTrue(started.wait(timeout=5))
            barrier.close()
            self.assertEqual(barrier.state, BarrierState.CLOSED)
            release.set()
            self.assertEqual(future.result(timeout=5), 9)
        self.assertFalse(barrier.report()["write_authorized"])
        self.assertEqual(barrier.in_flight, 0)

    def test_worker_error_still_releases_ticket(self):
        barrier = OfflineMaintenanceBarrier()
        def broken():
            raise RuntimeError('test failure')
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = submit_maintenance_work(pool, barrier, broken)
            with self.assertRaises(RuntimeError):
                future.result(timeout=5)
        self.assertEqual(barrier.in_flight, 0)

    def test_closed_barrier_rejects_even_cached_status(self):
        barrier = OfflineMaintenanceBarrier()
        barrier.close()
        with self.assertRaises(AdmissionRefused):
            admit_maintenance_work(barrier, {'command': 'status'})

    def test_no_maintenance_command_is_added(self):
        from player_service import COMMANDS
        self.assertFalse(any('maintenance' in command for command in COMMANDS))


if __name__ == '__main__':
    unittest.main()
