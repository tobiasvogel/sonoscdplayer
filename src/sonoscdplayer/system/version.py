"""SonosCDPlayer version: the VERSION file plus, in a git checkout, the commit.

`python3 -m sonoscdplayer.system.version` (from src/) prints it. The player logs it at start and puts it
in its status reply, so it is always clear which code runs on a device.
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

from sonoscdplayer.system.package_paths import REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT
_VERSION = re.compile(r'\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?')


def release(root: Path = ROOT) -> str:
    """The number in VERSION, or 'unknown'."""
    try:
        text = (root / 'VERSION').read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        return 'unknown'
    return text if _VERSION.fullmatch(text) else 'unknown'


def commit(root: Path = ROOT) -> str | None:
    """Short commit and '-dirty' for local changes; None outside git."""
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                              text=True, timeout=2, check=True).stdout.strip()
    try:
        head = git('rev-parse', '--short=7', 'HEAD')
        dirty = git('status', '--porcelain', '--untracked-files=no')
    except (OSError, subprocess.SubprocessError):
        return None
    return head + ('-dirty' if dirty else '') if head else None


@lru_cache(maxsize=1)
def info() -> dict:
    """{'version': '0.9.0', 'commit': 'abc1234' or None}; read once per process."""
    return {'version': release(), 'commit': commit()}


def describe() -> str:
    data = info()
    return data['version'] + (f' ({data["commit"]})' if data['commit'] else '')


if __name__ == '__main__':
    print('SonosCDPlayer ' + describe())
