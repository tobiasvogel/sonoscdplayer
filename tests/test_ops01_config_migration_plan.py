"""OPS-01 plan: offline only, no hardware, no writes, no credential echoes."""
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from tools.config_dry_run import main
from tools.config_migration_plan import plan_migration


class MigrationPlanTests(unittest.TestCase):
    def test_whitelisted_inert_default_is_proposed_only(self):
        old = {'audio_startup': {'prebuffer_before_sonos': True}}
        new = {'audio_startup': {'prebuffer_before_sonos': True,
                                  'pcm_timeout_seconds': 45}}
        report = plan_migration(old, new)
        self.assertEqual(report['candidates_for_explicit_review'],
                         ['audio_startup.pcm_timeout_seconds'])
        self.assertEqual(report['status'], 'review_required')
        self.assertFalse(report['changes_applied'])
        self.assertFalse(report['apply_supported'])
        self.assertNotIn('pcm_timeout_seconds', old['audio_startup'])

    def test_changed_whitelisted_default_is_not_approved(self):
        report = plan_migration({}, {'audio_startup': {'pcm_timeout_seconds': 90}})
        self.assertEqual(report['candidates_for_explicit_review'], [])
        self.assertEqual(report['unapproved_missing_count'], 1)

    def test_experimental_service_flag_never_added(self):
        old = {'service': {'shutdown_policy': 'stop_owned'}}
        new = {'service': {'shutdown_policy': 'stop_owned',
                           'experimental_safe_screen': True}}
        report = plan_migration(old, new)
        self.assertEqual(report['protected_missing_count'], 1)
        self.assertEqual(report['candidates_for_explicit_review'], [])
        self.assertNotIn('experimental_safe_screen', json.dumps(report))

    def test_current_main_disabled_experimental_shutdown_is_also_protected(self):
        old = {'service': {'shutdown_policy': 'stop_owned'}}
        new = {'service': {'shutdown_policy': 'stop_owned',
                           'experimental_safe_screen': False}}
        report = plan_migration(old, new)
        self.assertEqual(report['protected_missing_count'], 1)
        self.assertEqual(report['candidates_for_explicit_review'], [])
        self.assertFalse(report['apply_supported'])
        self.assertTrue(report['requires_explicit_approval'])
        self.assertNotIn('experimental_safe_screen', json.dumps(report))

    def test_existing_shutdown_choice_is_never_rewritten(self):
        old = {'service': {'shutdown_policy': 'stop_owned',
                           'experimental_safe_screen': True}}
        new = {'service': {'shutdown_policy': 'stop_owned',
                           'experimental_safe_screen': False}}
        before = json.dumps(old, sort_keys=True)
        report = plan_migration(old, new)
        self.assertEqual(json.dumps(old, sort_keys=True), before)
        self.assertEqual(report['candidates_for_explicit_review'], [])
        self.assertFalse(report['changes_applied'])

    def test_spin_down_remains_opt_in_even_with_reference_value(self):
        old = {'drive_power': {'keepalive_interval_seconds': 10}}
        new = {'drive_power': {'keepalive_interval_seconds': 10,
                               'idle_spin_down_seconds': 300}}
        report = plan_migration(old, new)
        self.assertEqual(report['protected_missing_count'], 1)
        self.assertFalse(report['apply_supported'])

    def test_existing_secrets_and_arbitrary_keys_not_echoed(self):
        old = {'credentials': {'private_uid': 'MY_SECRET_VALUE'},
               'custom local name': {'custom_token': 'SECRET_TOKEN'}}
        new = {'credentials': {'private_uid': True},
               'new custom name': {'token': 'PROPOSED_SECRET_VALUE'}}
        report = plan_migration(old, new)
        serialized = json.dumps(report)
        self.assertEqual(report['extra_local_keys_count'], 1)
        self.assertEqual(report['unapproved_missing_count'], 1)
        for forbidden in ('MY_SECRET_VALUE', 'SECRET_TOKEN',
                          'PROPOSED_SECRET_VALUE', 'private_uid',
                          'custom_token', 'new custom name'):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(report['type_conflicts'], [{'key': '[redacted]'}])

    def test_nested_existing_values_never_replaced(self):
        old = {'led_outputs': {'ambient': {'timeout_ms': 250, 'other': 4}}}
        new = {'led_outputs': {'ambient': {'timeout_ms': 150}}}
        before = json.dumps(old, sort_keys=True)
        report = plan_migration(old, new)
        self.assertEqual(json.dumps(old, sort_keys=True), before)
        self.assertEqual(report['extra_local_keys_count'], 1)
        self.assertEqual(report['candidates_for_explicit_review'], [])

    def test_empty_differences_compatible(self):
        same = {'a': {'b': 1}, 'flag': False}
        report = plan_migration(same, same)
        self.assertEqual(report['status'], 'compatible')
        self.assertEqual(report['existing_leaf_keys_count'], 2)
        self.assertFalse(report['requires_explicit_approval'])

    def test_wrong_template_types_are_blocked(self):
        old = {'audio_startup': {'prebuffer_before_sonos': True}}
        new = {'audio_startup': {'prebuffer_before_sonos': True,
                                  'pcm_timeout_seconds': True}}
        report = plan_migration(old, new)
        self.assertEqual(report['unapproved_missing_count'], 1)
        self.assertEqual(report['candidates_for_explicit_review'], [])

    def test_reject_non_table_inputs(self):
        with self.assertRaises(ValueError):
            plan_migration([], {})

    def test_direct_script_invocation_without_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / 'existing.toml'
            template = Path(directory) / 'reference.toml'
            current.write_text('[service]\nshutdown_policy="stop_owned"\n')
            template.write_text('[service]\nshutdown_policy="stop_owned"\n'
                                'experimental_safe_screen=true\n')
            script = Path(__file__).resolve().parents[1] / 'src' / 'tools' / 'config_dry_run.py'
            env = os.environ.copy()
            env.pop('PYTHONPATH', None)
            completed = subprocess.run(
                [sys.executable, str(script), '--current', str(current),
                 '--template', str(template), '--plan'],
                cwd=directory, env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 1, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload['protected_missing_count'], 1)
            self.assertFalse(payload['changes_applied'])
            self.assertNotIn(str(current), completed.stdout)

    def test_cli_plan_mode_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / 'old.toml'
            reference = Path(directory) / 'new.toml'
            old.write_text('[service]\nshutdown_policy="stop_owned"\n')
            reference.write_text(
                '[service]\nshutdown_policy="stop_owned"\n'
                'experimental_safe_screen=true\n')
            old_bytes, old_time = old.read_bytes(), old.stat().st_mtime_ns
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--current', str(old), '--template', str(reference),
                             '--plan'])
            self.assertEqual(code, 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload['protected_missing_count'], 1)
            self.assertFalse(payload['changes_applied'])
            self.assertEqual(old.read_bytes(), old_bytes)
            self.assertEqual(old.stat().st_mtime_ns, old_time)
            self.assertNotIn(str(old), output.getvalue())


if __name__ == '__main__':
    unittest.main()
