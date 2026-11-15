import unittest
from unittest.mock import patch
import frontends
from src.integrations.outputs import oled_status

class FrontendsContinuousTest(unittest.TestCase):
    def test_default_continuous(self):
        args=frontends.parser().parse_args(['--dry-run'])
        self.assertIsNone(args.seconds)
        jobs=dict(frontends.commands(args))
        for name in ('led','oled'):
            cmd=jobs[name]
            self.assertEqual(cmd[cmd.index('--seconds')+1], '0')

    def test_explicit_duration_belongs_to_supervisor_only(self):
        args=frontends.parser().parse_args(['--hardware','--seconds','180'])
        self.assertEqual(args.seconds,180)
        jobs=dict(frontends.commands(args))
        self.assertEqual(jobs['oled'][jobs['oled'].index('--seconds')+1], '0')
        self.assertEqual(jobs['led'][jobs['led'].index('--seconds')+1], '0')

    def test_bad_duration_rejected(self):
        for duration in ('0','-1','nan','inf'):
            with self.subTest(duration=duration), self.assertRaises(SystemExit):
                frontends.main(['--dry-run','--seconds',duration])

    def test_oled_zero_duration_allowed_in_dry_run(self):
        with patch.object(oled_status,'request_status',side_effect=RuntimeError('exit')):
            with self.assertRaises(RuntimeError):
                oled_status.main(['--seconds','0','--splash-seconds','0'])
