import tempfile
import unittest
from pathlib import Path

from drive_power import DrivePowerManager, load_config, parse_power_page

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

if __name__=='__main__':unittest.main()
