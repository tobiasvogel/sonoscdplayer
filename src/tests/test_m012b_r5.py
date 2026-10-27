"""M0.12b-r5: partial mode-page output and reader/keepalive coordination."""
import multiprocessing
import tempfile
import unittest
from unittest.mock import patch
from drive_access import drive_lock
from drive_power import DrivePowerManager
from player_controller import ProcessServer
from tests.test_m012b_r3 import PAGE, FakeRun


class TestR5(unittest.TestCase):
    def cfg(self):
        return {'enabled': True, 'strategy': 'auto', 'interval': 10.0, 'overrides': {}}

    def test_exit_five_is_partial_read_only(self):
        def run(args, **kwargs):
            r = FakeRun(PAGE)(args, **kwargs)
            if args[0] == 'sdparm':
                r.returncode = 5
            return r
        manager = DrivePowerManager('/dev/sr0', self.cfg(), runner=run)
        manager.start()
        s = manager.snapshot(reader_active=False)
        self.assertEqual(s['power_condition_probe'], 'partial')
        self.assertEqual(s['power_condition_probe_exit_code'], 5)
        self.assertTrue(s['supported']['power_condition_read'])
        self.assertFalse(s['supported']['power_condition_write'])
        self.assertEqual(s['active_strategy'], 'keepalive')
        self.assertEqual(s['power_condition_fields']['IACT']['value'], 370)

    def test_exit_five_never_allows_mode_page_write(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            fake = FakeRun('Power condition mode page:\n IACT 370 [cha: y, def:370]\n')
            r = fake(args, **kwargs)
            if args[0] == 'sdparm':r.returncode=5
            return r
        cfg = self.cfg(); cfg['overrides']={'IACT':0}
        m=DrivePowerManager('/dev/sr0',cfg,runner=run)
        m.start()
        self.assertEqual(m.active_strategy,'keepalive')
        self.assertFalse(any('--set=' in part for a in calls for part in a))

    def test_reader_state_follows_read_events(self):
        p=ProcessServer('.', '/dev/sr0', 'config.toml', 8765)
        class Live:
            def poll(self):return None
        p.proc=Live()
        p._observe_line('[+ 0.0s] READER started')
        self.assertTrue(p.reader_active())
        p._observe_line('[+ 33.0s] READER end written=30491328/30491328 error=None')
        self.assertFalse(p.reader_active())
        p._observe_line('[+ 0.0s] READER started')
        self.assertTrue(p.reader_active())

    def test_lock_busy_suppresses_keepalive_and_then_allows_it(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory}):
                clock=[0.0]
                fake=FakeRun()
                m=DrivePowerManager('/dev/sr0',self.cfg(),runner=fake,clock=lambda:clock[0])
                m.start();clock[0]=11.0
                with drive_lock('/dev/sr0'):
                    m.tick(reader_active=False)
                # flock in same process on a distinct fd still conflicts on Linux.
                self.assertEqual(sum(c[0]=='sg_start' for c in fake.calls),1)
                m.tick(reader_active=False)
                self.assertEqual(sum(c[0]=='sg_start' for c in fake.calls),2)

    def test_probe_result_survives_successful_keepalive(self):
        def run(args, **kwargs):
            r=FakeRun(PAGE)(args, **kwargs)
            if args[0]=='sdparm':r.returncode=5
            return r
        clock=[0.0]
        m=DrivePowerManager('/dev/sr0',self.cfg(),runner=run,clock=lambda:clock[0])
        m.start();clock[0]=12
        m.tick()
        self.assertEqual(m.snapshot(reader_active=False)['power_condition_probe'], 'partial')
        self.assertEqual(m.snapshot(reader_active=False)['power_condition_probe_exit_code'],5)

if __name__=='__main__':unittest.main()
