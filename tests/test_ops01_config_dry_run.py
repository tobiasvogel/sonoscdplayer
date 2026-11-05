"""Offline, hardware-free OPS-01 config inspector tests."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from tools.config_dry_run import (InspectionError, effective_config_path,
                                  inspect, main, read_toml)


class StructureTests(unittest.TestCase):
    def test_added_keys_are_named_without_values(self):
        report = inspect({'drive_power': {'enabled': True}},
                         {'drive_power': {'enabled': False, 'interval': 10}})
        self.assertEqual(report['status'], 'compatible')
        self.assertEqual(report['missing_template_keys'], ['drive_power.interval'])
        self.assertNotIn('False', json.dumps(report))

    def test_type_conflicts_and_extra_keys_require_review(self):
        report = inspect({'drive_power': 4, 'private_password': 'VERYSECRET'},
                         {'drive_power': {'enabled': True}})
        self.assertEqual(report['status'], 'review_required')
        self.assertEqual(report['type_conflicts'][0]['key'], 'drive_power')
        self.assertEqual(report['extra_current_keys_count'], 1)
        self.assertNotIn('VERYSECRET', str(report))
        self.assertNotIn('private_password', str(report))

    def test_invalid_key_names_are_redacted(self):
        report = inspect({}, {'private.name': {'token': 'SECRET'}})
        self.assertEqual(report['redacted_key_paths_count'], 1)
        self.assertNotIn('private.name', json.dumps(report))

    def test_nested_extras_are_not_silently_ignored(self):
        report = inspect({'known': {'a': 1, 'extra': {'secret': 'SECRET'}}},
                         {'known': {'a': 1}})
        self.assertEqual(report['status'], 'review_required')
        self.assertEqual(report['extra_current_keys_count'], 1)


class FileTests(unittest.TestCase):
    def test_regular_file_unmodified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'current.toml'
            text = '[drive_power]\nenabled=true\n'
            path.write_text(text)
            before = path.stat().st_mtime_ns
            self.assertEqual(read_toml(path), {'drive_power': {'enabled': True}})
            self.assertEqual(path.read_text(), text)
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'current'
            path.write_text('key=1')
            link = Path(directory) / 'link'
            link.symlink_to(path)
            with self.assertRaises(InspectionError):
                read_toml(link)

    def test_invalid_toml_and_directory_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid'
            path.write_text('[invalid\n')
            with self.assertRaises(InspectionError):
                read_toml(path)
            with self.assertRaises(InspectionError):
                read_toml(directory)

    def test_oversize_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'large'
            path.write_bytes(b'k' * (1024 * 1024 + 1))
            with self.assertRaises(InspectionError):
                read_toml(path)


class SystemdTests(unittest.TestCase):
    def test_effective_config_path(self):
        line = ('ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -u '
                'player_service.py --config /home/pi/sonoscdplayer/player.toml '
                '--socket /run/sonoscdplayer/control.sock ; ignore_errors=no ; '
                'start_time=[...] ; stop_time=[n/a] }\n')
        def runner(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout=line, stderr='')
        self.assertEqual(str(effective_config_path(
            'sonoscdplayer-player.service', runner=runner)),
            '/home/pi/sonoscdplayer/player.toml')

    def test_unapproved_service_and_relative_config_rejected(self):
        with self.assertRaises(InspectionError):
            effective_config_path('other.service')
        bad = 'ExecStart={ argv[]=python3 --config relative.toml ; ignore_errors=no }'
        with self.assertRaises(InspectionError):
            effective_config_path('sonoscdplayer-player.service',
                runner=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=bad))


class CLITests(unittest.TestCase):
    def test_no_secret_values_names_or_current_file_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.toml'
            template = Path(directory) / 'template.toml'
            path.write_text('password="TOPSECRET"\n[drive_power]\nenabled=true\n')
            template.write_text('[drive_power]\nenabled=true\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--current', str(path), '--template', str(template)])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())['status'], 'review_required')
            self.assertNotIn('TOPSECRET', output.getvalue())
            self.assertNotIn('password', output.getvalue())
            self.assertNotIn(str(path), output.getvalue())

    def test_malformed_template_is_not_echoed(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / 'current'
            template = Path(directory) / 'template'
            current.write_text('password="TOPSECRET"')
            template.write_text('invalid=[')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--current', str(current), '--template', str(template)])
            self.assertEqual(code, 2)
            self.assertNotIn('TOPSECRET', output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['status'], 'error')


if __name__ == '__main__':
    unittest.main()
