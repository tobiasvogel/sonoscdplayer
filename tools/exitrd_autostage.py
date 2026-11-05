#!/usr/bin/env python3
"""Installed systemd one-shot: stage a verified, root-owned exitrd into RAM.

No auto shutdown, no build and no persistent mounted root. Never stage when
the existing systemd exitrd slot is occupied. This file and dependencies
must be installed root-owned (not executed from the writable source tree).
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tomllib

import exitrd_once

EXPECTED = ('safe-approved=0', 'diagnostic=0', 'unverified-safe=1', 'delay-seconds=3')


class StageError(Exception):
    pass


def enabled(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        raise StageError('player config missing or symlinked')
    with path.open('rb') as source:
        data = tomllib.load(source)
    service = data.get('service', {})
    if not isinstance(service, dict):
        raise StageError('invalid [service] config')
    value = service.get('experimental_safe_screen', False)
    if not isinstance(value, bool):
        raise StageError('experimental_safe_screen must be boolean')
    return value


def verify_installed(binary: Path, sha_file: Path) -> str:
    directory = binary.parent
    if (directory.is_symlink() or not directory.is_dir() or
            directory.stat().st_uid != 0 or
            stat.S_IMODE(directory.stat().st_mode) & 0o022):
        raise StageError('installed directory not trusted')
    for path in (binary, sha_file):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise StageError('installed binary/hash must be root-owned regular files')
    digest = sha_file.read_text(encoding='ascii').strip()
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise StageError('bad installed hash')
    if hashlib.sha256(binary.read_bytes()).hexdigest() != digest:
        raise StageError('installed SHA-256 mismatch')
    result = subprocess.run([str(binary), '--check'], check=True,
                            capture_output=True, text=True, timeout=10)
    words = result.stdout.split()
    if any(words.count(t) != 1 for t in EXPECTED):
        raise StageError('binary not an unverified 3-second build')
    return digest


def stage(config: Path, binary: Path, sha_file: Path) -> None:
    if os.geteuid() != 0:
        raise StageError('root required for staging')
    if not enabled(config):
        print('experimental_safe_screen=false: not staging')
        return
    digest = verify_installed(binary, sha_file)
    # Any existing staged /run/initramfs mount is rejected, never overwritten.
    payload, actual, preserved = exitrd_once.precheck(binary, digest, activating=True)
    if digest != actual:
        raise StageError('preflight digest mismatch')
    print('WARNING: experimental display precedes final poweroff; wait for ACT LED')
    exitrd_once.arm_once(payload, digest, preserved)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--sha-file', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        stage(args.config, args.binary, args.sha_file)
        return 0
    except (StageError, exitrd_once.PreflightError, OSError,
            ValueError, subprocess.SubprocessError) as exc:
        print(f'exitrd-autostage: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
