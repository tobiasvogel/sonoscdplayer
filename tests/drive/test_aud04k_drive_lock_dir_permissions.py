"""AUD-04k: restrictive umask must not disable drive locking."""
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import sonoscdplayer.drive.drive_lock as drive_locks


def test_existing_fallback_0600_is_repaired(monkeypatch, tmp_path):
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.setattr(drive_locks.tempfile, 'gettempdir', lambda: str(tmp_path))
    directory = tmp_path / f'sonoscdplayer-{os.getuid()}'
    directory.mkdir(mode=0o700)
    directory.chmod(0o600)
    with drive_locks.drive_lock('/dev/sr0') as acquired:
        assert acquired
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert (directory / 'sonoscdplayer-drive-sr0.lock').is_file()


def test_new_fallback_under_restrictive_umask(tmp_path):
    # A subprocess avoids changing pytest's process-global umask.
    code = """
import os
from pathlib import Path
import sonoscdplayer.drive.drive_lock as locks
locks.tempfile.gettempdir = lambda: os.environ['AUD04K_TMP']
os.umask(0o177)
with locks.drive_lock('/dev/sr0') as acquired:
    assert acquired
root = Path(os.environ['AUD04K_TMP']) / f'sonoscdplayer-{os.getuid()}'
assert root.stat().st_mode & 0o777 == 0o700
"""
    env = dict(os.environ, AUD04K_TMP=str(tmp_path))
    env.pop('XDG_RUNTIME_DIR', None)
    proc = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_xdg_runtime_dir_not_modified(monkeypatch, tmp_path):
    directory = tmp_path / 'runtime'
    directory.mkdir(mode=0o700)
    directory.chmod(0o600)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(directory))
    try:
        with pytest.raises(PermissionError):
            drive_locks.lock_path('/dev/sr0')
        assert stat.S_IMODE(directory.stat().st_mode) == 0o600
    finally:
        directory.chmod(0o700)


def test_insecure_fallback_not_repaired(monkeypatch, tmp_path):
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.setattr(drive_locks.tempfile, 'gettempdir', lambda: str(tmp_path))
    directory = tmp_path / f'sonoscdplayer-{os.getuid()}'
    directory.mkdir(mode=0o700)
    directory.chmod(0o777)
    try:
        with pytest.raises(PermissionError):
            drive_locks.lock_path('/dev/sr0')
    finally:
        directory.chmod(0o700)
