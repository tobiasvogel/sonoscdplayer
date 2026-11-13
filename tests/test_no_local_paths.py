"""Guard: versioned files must not contain installation-specific paths or accounts.

Local paths, service accounts and similar deployment details are passed
explicitly at render/install time (see tools/render_runtime_units.py).
"""
from pathlib import Path
import re
import subprocess
import unittest

from sonos_ui.ui_config import DEFAULT_CONFIG, UIConfig, load_ui_config


ROOT = Path(__file__).resolve().parents[1]
# (pattern, label, only check files below this top-level directory or None)
FORBIDDEN = (
    (re.compile(r'/home/[A-Za-z0-9_.-]+'), 'home directory path', None),
    (re.compile(r'/Users/[A-Za-z0-9_.-]+'), 'home directory path', None),
    (re.compile(r'^User=(?!@SONOSCDPLAYER_USER@$|root$).+$', re.MULTILINE),
     'hardcoded service account', 'systemd'),
    (re.compile(r'subject\.user === "(?!@SONOSCDPLAYER_USER@")'),
     'hardcoded polkit account', 'systemd'),
)
TEXT_SUFFIXES = {'', '.py', '.toml', '.md', '.service', '.conf', '.rules',
                 '.sh', '.target', '.example', '.c', '.h', '.json', '.lst'}


def tracked_text_files():
    try:
        names = subprocess.run(['git', 'ls-files'], cwd=ROOT, check=True,
                               capture_output=True, text=True).stdout.split()
        paths = [ROOT / name for name in names]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in ROOT.rglob('*') if '.git' not in p.parts]
    for path in paths:
        if path.is_file() and path.suffix in TEXT_SUFFIXES and \
                path != Path(__file__).resolve():
            yield path


class NoLocalPathsTests(unittest.TestCase):
    def test_repository_contains_no_local_paths_or_accounts(self):
        findings = []
        for path in tracked_text_files():
            try:
                text = path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            top = path.relative_to(ROOT).parts[0]
            for pattern, label, scope in FORBIDDEN:
                if scope is not None and top != scope:
                    continue
                for match in pattern.finditer(text):
                    line = text.count('\n', 0, match.start()) + 1
                    findings.append(f'{path.relative_to(ROOT)}:{line}: {label}')
        self.assertEqual(findings, [])

    def test_ui_config_has_no_builtin_installation_path(self):
        self.assertIsNone(DEFAULT_CONFIG)
        self.assertEqual(load_ui_config(), UIConfig())
        self.assertEqual(load_ui_config(None), UIConfig())


if __name__ == '__main__':
    unittest.main()
