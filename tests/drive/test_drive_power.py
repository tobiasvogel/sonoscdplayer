"""CD drive power management: capability probing, mode pages, keepalive and idle configuration (simulated SCSI)."""
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
from sonoscdplayer.drive.power import DrivePowerManager, load_config, parse_power_page
from sonoscdplayer.drive.drive_lock import drive_lock
from sonoscdplayer.player.controller import TrackServerProcess
from sonoscdplayer.ui.display_service import StatusHold, request_status
PAGE = ''' /dev/sr0: TSSTcorp CDDVDW TS-U633J [cd/dvd]\nPower condition mode page:\n IACT 370 [cha: n, def:370]\n SZCT 430 [cha: n, def:430]\n IDLE_A 1 [cha: n, def: 1]\n'''
CHANGEABLE = '''Power condition mode page:\n IACT 370 [cha: y, def:370]\n SZCT 430 [cha: n, def:430]\n'''



class FakeRun:
    def __init__(self, page=PAGE, start_ok=True):
        self.page, self.calls, self.start_ok = page, [], start_ok
    def __call__(self, args, **kwargs):
        self.calls.append(args)
        class Result:
            returncode = 0
            stderr = ''
            stdout = ''
        result = Result()
        if args[0] == 'sdparm' and '--page=0x1a' in args and not any(v.startswith('--set') for v in args):
            result.stdout = self.page
        elif args[0] == 'sg_start' and not self.start_ok:
            result.returncode = 1
        elif args[0] == 'sdparm':
            name, value = args[1][6:].split('=')
            import re
            self.page = re.sub(r'(' + name + r'\s+)\d+', r'\g<1>' + value, self.page)
        return result


class Tests(unittest.TestCase):
    def cfg(self, strategy='auto', overrides=None, enabled=True):
        return {'enabled':enabled, 'strategy':strategy, 'interval':10.0, 'overrides':overrides or {}}
    def test_parse_changeable(self):
        self.assertFalse(parse_power_page(PAGE)['IACT']['changeable'])
        self.assertTrue(parse_power_page(CHANGEABLE)['IACT']['changeable'])
    def test_disabled_has_no_commands(self):
        fake = FakeRun()
        m = DrivePowerManager('/dev/sr0', self.cfg(enabled=False), runner=fake)
        m.start(); m.tick(); m.close()
        self.assertEqual(fake.calls, [])
    def test_auto_falls_back_for_readonly_drive(self):
        fake = FakeRun()
        now = [0.0]
        m = DrivePowerManager('/dev/sr0', self.cfg(), runner=fake, clock=lambda:now[0])
        m.start()
        self.assertEqual(m.active_strategy, 'keepalive')
        self.assertFalse(m.supported['power_condition_write'])
        now[0] = 12
        m.tick(reader_active=True)
        self.assertEqual(len([x for x in fake.calls if x[0]=='sg_start']),1)
        m.tick(reader_active=False)
        self.assertEqual(len([x for x in fake.calls if x[0]=='sg_start']),2)
    def test_auto_write_verify_and_restore(self):
        fake=FakeRun(CHANGEABLE)
        m=DrivePowerManager('/dev/sr0',self.cfg(overrides={'IACT':0}),runner=fake)
        m.start()
        self.assertEqual(m.active_strategy,'power_condition')
        self.assertIn(['sdparm','--set=IACT=0','--page=0x1a','/dev/sr0'],fake.calls)
        m.close()
        self.assertIn(['sdparm','--set=IACT=370','--page=0x1a','/dev/sr0'],fake.calls)
    def test_no_unsafe_write_when_read_only(self):
        fake=FakeRun()
        m=DrivePowerManager('/dev/sr0',self.cfg(overrides={'IACT':0}),runner=fake)
        m.start()
        self.assertEqual(m.active_strategy,'keepalive')
        self.assertFalse(any(any(v.startswith('--set') for v in call) for call in fake.calls))
    def test_no_fallback_for_explicit_power_condition(self):
        fake=FakeRun()
        m=DrivePowerManager('/dev/sr0',self.cfg('power_condition',{'IACT':0}),runner=fake)
        m.start()
        self.assertEqual(m.active_strategy,'off')
        self.assertFalse(any(c[0]=='sg_start' for c in fake.calls))
    def test_power_config_rejects_bad_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.toml'
            p.write_text('[drive_power]\nenabled=true\n[drive_power.mode_page_overrides]\nEVIL=99\n')
            with self.assertRaises(ValueError):load_config(p)
            p.write_text('[drive_power]\nenabled=true\nstrategy="auto"\nkeepalive_interval_seconds=10\n')
            self.assertTrue(load_config(p)['enabled'])
    def test_missing_binary_degrades_without_crash(self):
        def missing(*a,**kw): raise FileNotFoundError('sg_start')
        m=DrivePowerManager('/dev/sr0',self.cfg(),runner=missing)
        m.start()
        self.assertEqual(m.active_strategy,'off')
        self.assertIsNotNone(m.last_error)
    def test_snapshot_cached_only(self):
        fake=FakeRun()
        m=DrivePowerManager('/dev/sr0',self.cfg(),runner=fake)
        self.assertFalse(m.snapshot(reader_active=False)['reader_active'])
        self.assertEqual(fake.calls,[])


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


class ModePageKeepaliveTests(unittest.TestCase):
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
        p=TrackServerProcess('.', '/dev/sr0', 'config.toml', 8765)
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


class DriveStatusGraceTests(unittest.TestCase):
    def test_retain_recent_status(self):
        hold=StatusHold(4)
        value={'player':{'mode':'PLAYING'}}
        self.assertIs(hold.success(value,10),value)
        self.assertIs(hold.failure(13.99),value)
        self.assertIsNone(hold.failure(14))
    def test_offline_without_prior_success(self):
        self.assertIsNone(StatusHold().failure(100))
    def test_refresh_success_resets_grace(self):
        hold=StatusHold(4)
        hold.success({'player':{'mode':'PLAYING'}},1)
        hold.success({'player':{'mode':'PAUSED'}},5)
        self.assertEqual(hold.failure(8)['player']['mode'],'PAUSED')
    def test_zero_grace_is_immediate(self):
        hold=StatusHold(0)
        hold.success({},2)
        self.assertIsNone(hold.failure(2))
    def test_config_default_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'config.toml'; f.write_text('[service]\nshutdown_policy="stop_owned"\n')
            self.assertTrue(load_config(f)['enabled'])
    def test_explicit_disabled_respected(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'config.toml'; f.write_text('[drive_power]\nenabled=false\n')
            self.assertFalse(load_config(f)['enabled'])
    def test_bundled_config_enabled(self):
        self.assertTrue(load_config(Path(__file__).resolve().parents[2]/'config/player.example.toml')['enabled'])


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
        with patch('sonoscdplayer.drive.power.Path.exists', return_value=True):
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
        with patch('sonoscdplayer.drive.power.Path.exists', return_value=False):
            with self.assertRaises(RuntimeError):
                manager.rebind_device('/dev/sr1')
        self.assertEqual(manager.device, '/dev/sr0')


class DefaultIdleConfigTests(unittest.TestCase):

    def load_text(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "player.toml"
            path.write_text(text, encoding="utf-8")
            return load_config(path)

    def test_shipped_default_is_120_seconds(self):
        config = load_config(ROOT / "config/player.example.toml")
        self.assertTrue(config["enabled"])
        self.assertEqual(config["strategy"], "auto")
        self.assertEqual(
            config["idle_spin_down_seconds"], 120.0
        )

    def test_custom_60_seconds(self):
        config = self.load_text(
            "[drive_power]\n"
            "enabled = true\n"
            'strategy = "auto"\n'
            "idle_spin_down_seconds = 60\n"
        )
        self.assertEqual(
            config["idle_spin_down_seconds"], 60.0
        )

    def test_legacy_config_remains_unchanged(self):
        config = self.load_text(
            "[drive_power]\n"
            "enabled = true\n"
            'strategy = "auto"\n'
        )
        self.assertIsNone(
            config["idle_spin_down_seconds"]
        )

    def test_less_than_30_seconds_rejected(self):
        with self.assertRaises(ValueError):
            self.load_text(
                "[drive_power]\n"
                "enabled = true\n"
                'strategy = "auto"\n'
                "idle_spin_down_seconds = 5\n"
            )


if __name__ == '__main__':
    unittest.main()
