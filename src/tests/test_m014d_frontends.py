import unittest
import frontends

class FrontendTest(unittest.TestCase):
    def test_preview_only_outputs_without_button_commands(self):
        p=frontends.parser()
        a=p.parse_args(['--dry-run'])
        jobs=frontends.commands(a)
        self.assertEqual([n for n,_ in jobs], ['led','oled','buttons'])
        self.assertNotIn('--execute',jobs[0][1])
        self.assertNotIn('--commands',jobs[2][1])
    def test_opt_in_commands_forward_uid(self):
        a=frontends.parser().parse_args(['--hardware','--buttons','--commands','--uid','RINCON_TEST','--dry-run'])
        j=frontends.commands(a)
        self.assertIn('--ambient-auto',j[1][1])
        self.assertEqual(j[2][1][-4:],['--commands','--execute','--uid','RINCON_TEST'])
    def test_reject_commands_without_safety_flags(self):
        with self.assertRaises(SystemExit):
            frontends.main(['--dry-run','--commands','--uid','RINCON_TEST'])
    def test_default_frontends_not_started_from_dry_run(self):
        self.assertEqual(frontends.main(['--dry-run']),0)
